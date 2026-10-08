"""对话记忆: 完整历史 + 上下文窗口。

设计要点 (和之前的关键区别):
- `Memory` 在会话期内保留**全量**消息; 窗口 (`max_messages` / `max_tokens`) 只在
  "取出消息发给模型" 时计算 (`messages()` / `to_openai_list()`)。
- 持久化用 `all_messages()` 写全量。之前是 `add()` 时就地截断, 老消息在内存里先消失,
  再被覆盖式的 save 从库里删掉 —— 于是"持久化"只留下了最后一个窗口。

窗口算法: 先按条数, 再按粗略 token 预算从最老的整组开始丢, 且
`assistant(tool_calls)` 和它的 tool 结果必须同生共死 (拆开会让上游 400)。
"""
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
    # 在所属 session 内的单调序号, 由 Memory 分配; 持久化时作为主键的一部分。
    # 有了它, store 才能增量落库 (老消息即使已滑出窗口, 库里的行也不会被覆盖删除)。
    seq: int = 0

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
    """全量历史 + 滚动窗口 (窗口只影响发给模型多少)。"""

    def __init__(self, max_messages: int = 30, max_tokens: int = 6000):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self._messages: list[Message] = []   # 全量, 含已滑出窗口的老消息
        self._next_seq = 1

    # ---- mutators ----
    def add(self, role: str, content: str | None = None, **kw: Any) -> Message:
        msg = Message(role=role, content=content, **kw)
        self._assign_seq(msg)
        self._messages.append(msg)
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

    def load_message(self, msg: Message) -> Message:
        """直接放回一条已带序号的历史消息 (持久化层回填用)。

        `_next_seq` 会跟着往前推, 保证后续新消息的序号不会和库里的老行撞车。
        """
        self._assign_seq(msg)
        self._messages.append(msg)
        return msg

    def numbered(self) -> list[Message]:
        """补齐所有消息的序号并返回全量列表 (兼容直接 append 进来的消息)。"""
        for m in self._messages:
            self._assign_seq(m)
        return self._messages

    def _assign_seq(self, msg: Message) -> None:
        if msg.seq <= 0:
            msg.seq = self._next_seq
        if msg.seq >= self._next_seq:
            self._next_seq = msg.seq + 1

    def clear(self) -> None:
        self._messages.clear()
        self._next_seq = 1  # 与 store.clear 配套: 清空后序号从 1 重新开始

    # ---- accessors ----
    def all_messages(self) -> list[Message]:
        """全量历史 (持久化 / UI 全量展示用)。"""
        return list(self._messages)

    def messages(self) -> list[Message]:
        """窗口内的消息 (= 实际会发给模型的那部分)。"""
        return self._window()

    def to_openai_list(self) -> list[dict[str, Any]]:
        return [m.to_openai() for m in self._window()]

    # ---- 窗口计算 (纯读, 不改 self._messages) ----
    def _window(self) -> list[Message]:
        msgs = self._messages
        # 先按条数 (max_messages <= 0 视为不限制)
        if self.max_messages > 0 and len(msgs) > self.max_messages:
            msgs = msgs[-self.max_messages :]
        # 再按 token 预算, 从最老的整组开始丢
        while msgs and self._tokens(msgs) > self.max_tokens:
            units = self._units(msgs)
            if len(units) <= 1:
                break
            msgs = msgs[len(units[0]) :]
        # 兜底: 任何情况下都不把孤立 tool 消息留在开头, 否则 OpenAI 兼容后端会 400
        while msgs and msgs[0].role == "tool":
            msgs = msgs[1:]
        return msgs

    @staticmethod
    def _tokens(msgs: list[Message]) -> int:
        total = 0
        for m in msgs:
            total += Message.estimate_tokens(m.content)
            if m.tool_calls:
                total += Message.estimate_tokens(json.dumps(m.tool_calls, ensure_ascii=False))
        return total

    @staticmethod
    def _units(msgs: list[Message]) -> list[list[Message]]:
        """把消息切成原子组: assistant(tool_calls) 和它的 tool 结果必须同生共死。"""
        units: list[list[Message]] = []
        for m in msgs:
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

    def _approx_tokens(self) -> int:
        """窗口内的粗略 token 数 (测试 / 调试用)。"""
        return self._tokens(self._window())