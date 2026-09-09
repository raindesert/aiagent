"""Agent 主循环 (ReAct-style with native function calling)。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import (
    AgentConfig,
    ModelsConfig,
    entry_to_model_config,
    find_model_entry,
    load_models_config,
)
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
    def __init__(self, cfg: AgentConfig, models_cfg: ModelsConfig | None = None):
        self.cfg = cfg
        self.context = ContextManager(cfg.context)
        self.memory = Memory(
            max_messages=cfg.context.max_history_messages,
            max_tokens=cfg.context.max_history_tokens,
        )
        self.tools = ToolRegistry(cfg.tools)

        # 模型配置: 优先用传入的 models_cfg, 否则从 cfg.models_file 加载
        if models_cfg is None:
            models_cfg = load_models_config(cfg.models_file)
        self._models_cfg = models_cfg
        # 启动模型: 优先 cfg.default_model, 否则用 models.yaml 里的 default
        self._current_model_name = cfg.default_model or models_cfg.default
        self.model = self._build_model_client(self._current_model_name)

        log.info(
            "Agent '%s' 初始化完成, model=%s (%s), tools=%s",
            cfg.name, self._current_model_name, self.model.cfg.name, self.tools.names(),
        )

    # ---------- 模型管理 ----------
    def _build_model_client(self, name: str) -> ModelClient:
        entry = find_model_entry(self._models_cfg, name)
        return ModelClient(entry_to_model_config(entry))

    def switch_model(self, name: str) -> str:
        """切换到指定模型。返回人类可读的状态信息; 失败抛 ValueError。"""
        if name == self._current_model_name:
            return f"已经在 {name} 上, 无需切换"
        new_client = self._build_model_client(name)
        old = self._current_model_name
        old_model_name = self.model.cfg.name
        self.model = new_client
        self._current_model_name = name
        return f"已切换: {old} (model: {old_model_name}) → {name} (model: {new_client.cfg.name})"

    def list_models(self) -> list[str]:
        return [m.name for m in self._models_cfg.models]

    def model_info(self) -> list[dict]:
        """返回所有模型的摘要, 给 /models 命令展示。"""
        out = []
        for m in self._models_cfg.models:
            out.append({
                "name": m.name,
                "model": m.model,
                "base_url": m.base_url,
                "current": m.name == self._current_model_name,
            })
        return out

    @property
    def current_model_name(self) -> str:
        return self._current_model_name

    # ---------- 主循环 ----------
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
