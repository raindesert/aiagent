"""Web UI 的纯逻辑层: 消息视图 + 流式转次. 不 import streamlit, 便于离线单测。"""
from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from typing import Any

from .memory import Message

_TOOL_RESULT_LIMIT = 1000


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (截断, 共 {len(text)} 字符)"


def history_view(messages: list[Message]) -> list[dict[str, Any]]:
    """把记忆里的消息整理成 UI 可渲染的条目列表。

    system 不进视图; assistant 的 tool_calls 和对应的 tool 结果合成一条 "tool" 条目
    (按 tool_call_id 配对), 这样聊天气泡里只留人和助手的话。
    """
    out: list[dict[str, Any]] = []
    pending: dict[str, int] = {}          # tool_call_id -> out 里那条 tool 的下标
    for m in messages:
        if m.role == "user":
            out.append({"kind": "chat", "role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            if m.content:
                out.append({"kind": "chat", "role": "assistant", "content": m.content})
            for tc in m.tool_calls or []:
                fn = tc.get("function") or {}
                pending[tc.get("id") or ""] = len(out)
                out.append({
                    "kind": "tool",
                    "name": fn.get("name") or "?",
                    "arguments": _pretty_args(fn.get("arguments")),
                    "result": None,
                })
        elif m.role == "tool":
            result = _shorten(m.content or "", _TOOL_RESULT_LIMIT)
            idx = pending.pop(m.tool_call_id or "", None)
            if idx is None:
                # 父 assistant 已被截断丢掉, 只能单独显示结果
                out.append({"kind": "tool", "name": "?", "arguments": "", "result": result})
            else:
                out[idx]["result"] = result
    return out


def _pretty_args(raw: Any) -> str:
    if not raw:
        return ""
    if isinstance(raw, dict):
        raw = json.dumps(raw, ensure_ascii=False)
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False)
    except (ValueError, TypeError):
        return str(raw)


def stream_turn(agent: Any, user_input: str) -> Iterator[tuple[str, Any]]:
    """在一轮对话里持续产出事件: ("token", str) ... 最后 ("result", AgentResult)。

    agent.chat 是阻塞的, 放到工作线程里跑, 主线程靠队列取 token, 这样网页能边生成边刷新。
    模型没开 stream 时一个 token 都不会有, 结束时一次性把全文补上, UI 逻辑就不用分两种情况。
    """
    q: queue.Queue[tuple[str, Any]] = queue.Queue()
    done: dict[str, Any] = {}

    def worker() -> None:
        try:
            done["result"] = agent.chat(
                user_input, on_token=lambda t: q.put(("token", t))
            )
        except BaseException as e:  # 包括 SystemExit: 否则主线程会永久等下去
            done["error"] = e
        finally:
            q.put(("done", None))

    thread = threading.Thread(target=worker, name="agent-turn", daemon=True)
    thread.start()

    saw_token = False
    while True:
        kind, payload = q.get()
        if kind == "token":
            saw_token = True
            yield kind, payload
        else:
            break
    thread.join()

    if "error" in done:
        raise done["error"]
    result = done["result"]
    if not saw_token and result.content:
        yield "token", result.content
    yield "result", result
