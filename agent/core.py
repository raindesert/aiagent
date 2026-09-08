"""Agent 主循环 (ReAct-style with native function calling)。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import AgentConfig
from .context import ContextManager
from .memory import Memory
from .model import ModelClient, ToolCall
from .tools import ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    content: str
    iterations: int
    tool_calls_made: list[str]


class Agent:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.context = ContextManager(cfg.context)
        self.memory = Memory(
            max_messages=cfg.context.max_history_messages,
            max_tokens=cfg.context.max_history_tokens,
        )
        self.model = ModelClient(cfg.model)
        self.tools = ToolRegistry(cfg.tools)
        log.info(
            "Agent '%s' 初始化完成, model=%s, tools=%s",
            cfg.name, cfg.model.name, self.tools.names(),
        )

    def chat(self, user_input: str) -> AgentResult:
        self.memory.add_user(user_input)
        tool_calls_made: list[str] = []
        iterations = 0

        for i in range(self.cfg.loop.max_iterations):
            iterations = i + 1
            messages = self.context.build_messages(self.memory)
            response = self.model.chat(messages, tools=self.tools.schemas())

            # ---- 1. 纯文本回答 -> 结束 ----
            if not response.has_tool_calls:
                content = response.content or ""
                self.memory.add_assistant(content if content else None)
                return AgentResult(
                    content=content,
                    iterations=iterations,
                    tool_calls_made=tool_calls_made,
                )

            # ---- 2. 有 tool calls -> 执行并喂回 ----
            tc_dicts = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ]
            # assistant 消息里 content 可为空但字段要在
            self.memory.add_assistant(response.content or "", tool_calls=tc_dicts)

            for tc in response.tool_calls:
                tool_calls_made.append(tc.name)
                log.info("调用工具: %s(%s)", tc.name, tc.arguments)
                result = self.tools.execute(
                    tc.name, tc.arguments, timeout=self.cfg.loop.tool_timeout
                )
                feed = result.to_message()
                log.info("工具结果 (%s): %s", tc.name, feed[:200])
                self.memory.add_tool(feed, tool_call_id=tc.id)

            # 回到循环顶部, 让模型基于工具结果继续

        # 迭代耗尽
        return AgentResult(
            content="[agent] 达到最大迭代次数, 任务未完成",
            iterations=iterations,
            tool_calls_made=tool_calls_made,
        )

    def reset(self) -> None:
        self.memory.clear()
