"""对话记忆 + 截断策略。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Message:
    role: str                              # system | user | assistant | tool
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict] | None = None   # assistant 消息携带
    tool_call_id: str | None = None        # tool 消息携带
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_openai(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    @staticmethod
    def estimate_tokens(text: str | None) -> int:
        if not text:
            return 0
        # 粗略估计: 1 token ≈ 1.5 字符 (中文略多, 英文略少, 平衡一下)
        return max(1, len(text) * 2 // 3)


class Memory:
    """简单的滚动窗口 + 粗略 token 预算控制。"""

    def __init__(self, max_messages: int = 30, max_tokens: int = 6000):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self._messages: list[Message] = []

    # ---- mutators ----
    def add(self, role: str, content: str | None = None, **kw: Any) -> Message:
        msg = Message(role=role, content=content, **kw)
        self._messages.append(msg)
        self._truncate()
        return msg

    def add_user(self, content: str) -> Message:
        return self.add("user", content)

    def add_assistant(
        self,
        content: str | None,
        tool_calls: list[dict] | None = None,
    ) -> Message:
        return self.add("assistant", content, tool_calls=tool_calls)

    def add_tool(self, content: str, tool_call_id: str) -> Message:
        return self.add("tool", content, tool_call_id=tool_call_id)

    # ---- accessors ----
    def messages(self) -> list[Message]:
        return list(self._messages)

    def to_openai_list(self) -> list[dict[str, Any]]:
        return [m.to_openai() for m in self._messages]

    def clear(self) -> None:
        self._messages.clear()

    # ---- helpers ----
    def _approx_tokens(self) -> int:
        total = 0
        for m in self._messages:
            total += Message.estimate_tokens(m.content)
            if m.tool_calls:
                total += Message.estimate_tokens(json.dumps(m.tool_calls, ensure_ascii=False))
        return total

    def _units(self) -> list[list[Message]]:
        """把消息切成原子组: assistant(tool_calls) 和它的 tool 结果必须同生共死。"""
        units: list[list[Message]] = []
        for m in self._messages:
            parent_open = (
                m.role == "tool"
                and units
                and units[-1][-1].role in ("assistant", "tool")
                and any(x.role == "assistant" and x.tool_calls for x in units[-1])
            )
            if parent_open:
                units[-1].append(m)
            else:
                units.append([m])
        return units

    def _truncate(self) -> None:
        # 先按消息数
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]
        # 再按 token 预算: 从最老的整组开始丢, 不把 assistant(tool_calls) 和它的 tool 结果拆开
        while self._messages and self._approx_tokens() > self.max_tokens:
            units = self._units()
            if len(units) <= 1:
                break
            self._messages = self._messages[len(units[0]) :]
        # 兜底: 任何情况下都不把孤立 tool 消息留在开头, 否则 OpenAI 兼容后端会 400
        while self._messages and self._messages[0].role == "tool":
            self._messages.pop(0)
