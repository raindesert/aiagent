"""Agent 主循环 (ReAct-style with native function calling)。"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from .config import (
    AgentConfig,
    ModelsConfig,
    entry_to_model_config,
    find_model_entry,
    load_models_config,
)
from .context import ContextManager
from .memory import Memory
from .memory_store import MemoryStore
from .model import ModelClient, ToolCall
from .tools import ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    content: str
    iterations: int
    tool_calls_made: list[str]


class Agent:
    def __init__(
        self,
        cfg: AgentConfig,
        models_cfg: ModelsConfig | None = None,
        *,
        session_id: str = "default",
        store: MemoryStore | None = None,
        auto_save: bool = True,
    ):
        self.cfg = cfg
        self.context = ContextManager(cfg.context)
        self.tools = ToolRegistry(cfg.tools)

        # 模型配置
        if models_cfg is None:
            models_cfg = load_models_config(cfg.models_file)
        self._models_cfg = models_cfg
        self._current_model_name = cfg.default_model or models_cfg.default
        self.model = self._build_model_client(self._current_model_name)

        # 持久化记忆
        self.session_id = session_id
        self.store = store
        self.auto_save = auto_save
        self.memory = Memory(
            max_messages=cfg.context.max_history_messages,
            max_tokens=cfg.context.max_history_tokens,
        )
        if store is not None:
            loaded = store.load(
                session_id,
                max_messages=cfg.context.max_history_messages,
                max_tokens=cfg.context.max_history_tokens,
            )
            if loaded is not None and loaded.messages():
                self.memory = loaded
                log.info(
                    "从 store 加载 session %r 的 %d 条历史",
                    session_id, len(loaded.messages()),
                )
            else:
                log.info("session %r 是新的, 从空开始", session_id)
        else:
            log.info("Agent 初始化 (无持久化 store), memory 初始为空")

        log.info(
            "Agent '%s' 初始化完成, model=%s (%s), session=%r, tools=%s",
            cfg.name, self._current_model_name, self.model.cfg.name,
            session_id, self.tools.names(),
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

    # ---------- session 管理 ----------
    def save(self, title: str | None = None) -> None:
        """手动保存当前 session 到 store。"""
        if self.store is None:
            log.warning("没有 store, save 无效")
            return
        self.store.save(self.session_id, self.memory, title=title)

    def _auto_save_if_needed(self) -> None:
        if self.auto_save and self.store is not None:
            try:
                self.store.save(self.session_id, self.memory)
            except Exception as e:
                log.warning("自动保存失败: %s", e)

    def new_session(self, session_id: Optional[str] = None, title: str | None = None) -> str:
        """开始新 session: 自动保存当前, 然后切到新 session_id。

        Returns: 切换消息。
        """
        old = self.session_id
        if self.store is not None:
            try:
                self.store.save(old, self.memory, title=f"auto-{old}")
            except Exception as e:
                log.warning("保存旧 session 失败: %s", e)
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"
        self.session_id = sid
        self.memory = Memory(
            max_messages=self.cfg.context.max_history_messages,
            max_tokens=self.cfg.context.max_history_tokens,
        )
        return f"已开新 session: '{sid}' (旧 '{old}' 已自动保存)"

    def switch_session(self, session_id: str) -> str:
        """切换到已存在的 session: 自动保存当前, 加载目标。
        如果 session_id 不存在, 创建新的空 session。
        """
        if session_id == self.session_id:
            return f"已经在 session '{session_id}' 上, 无需切换"
        old = self.session_id
        # 1. 保存当前
        if self.store is not None:
            try:
                self.store.save(old, self.memory, title=f"auto-{old}")
            except Exception as e:
                log.warning("保存旧 session 失败: %s", e)
        # 2. 加载目标
        loaded = None
        if self.store is not None:
            loaded = self.store.load(
                session_id,
                max_messages=self.cfg.context.max_history_messages,
                max_tokens=self.cfg.context.max_history_tokens,
            )
        if loaded is not None and loaded.messages():
            self.memory = loaded
            status = f"载入 {len(loaded.messages())} 条历史"
        else:
            # 目标不存在, 开新的空 session
            self.memory = Memory(
                max_messages=self.cfg.context.max_history_messages,
                max_tokens=self.cfg.context.max_history_tokens,
            )
            status = "目标 session 不存在, 已开空 session"
        self.session_id = session_id
        return f"已切换 session: '{old}' → '{session_id}' ({status})"

    def list_sessions(self) -> list[dict]:
        if self.store is None:
            return []
        return self.store.list_sessions()

    # ---------- 主循环 ----------
    def chat(self, user_input: str, on_token: callable | None = None) -> AgentResult:
        """跑一轮对话。

        Args:
            user_input: 用户输入。
            on_token: 可选回调, 接收 (str) 每个 text token 触发; 触发流式输出。
        """
        self.memory.add_user(user_input)
        tool_calls_made: list[str] = []
        iterations = 0

        use_stream = on_token is not None and self.model.cfg.stream

        for i in range(self.cfg.loop.max_iterations):
            iterations = i + 1
            messages = self.context.build_messages(self.memory)
            if use_stream:
                response = self.model.chat_streaming(
                    messages, tools=self.tools.schemas(), on_token=on_token
                )
            else:
                response = self.model.chat(messages, tools=self.tools.schemas())

            # ---- 1. 纯文本回答 -> 结束 ----
            if not response.has_tool_calls:
                content = response.content or ""
                self.memory.add_assistant(content if content else None)
                self._auto_save_if_needed()
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
        result = AgentResult(
            content="[agent] 达到最大迭代次数, 任务未完成",
            iterations=iterations,
            tool_calls_made=tool_calls_made,
        )
        self._auto_save_if_needed()
        return result

    def reset(self) -> None:
        """清空当前 session 的 memory。"""
        self.memory.clear()
        # 同步到 store: 让旧 session 变空, 或者保留旧消息在 store 里?
        # 这里简单做: 同步清空, 等下次 chat 时再写
        if self.store is not None:
            try:
                self.store.save(self.session_id, self.memory)
            except Exception as e:
                log.warning("reset 时保存失败: %s", e)