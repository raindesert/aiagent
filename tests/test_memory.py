"""对话记忆截断 + SQLite 持久化。"""
from __future__ import annotations

import json

import pytest

from agent import Memory, Message


def tool_pair(mem: Memory, call_id: str = "c1") -> None:
    mem.add_assistant("调个工具", tool_calls=[{
        "id": call_id, "type": "function",
        "function": {"name": "get_current_time", "arguments": "{}"},
    }])
    mem.add_tool("2026-09-23 12:00:00 " + "x" * 200, tool_call_id=call_id)


# ---------- 截断 ----------
def test_message_count_cap():
    mem = Memory(max_messages=5, max_tokens=10**9)
    for i in range(9):
        mem.add_user(f"msg{i}")
    assert [m.content for m in mem.messages()] == ["msg4", "msg5", "msg6", "msg7", "msg8"]


def test_token_budget_is_respected():
    mem = Memory(max_messages=10**6, max_tokens=60)
    for i in range(8):
        mem.add_user(f"用户消息 {i} " + "长" * 30)
    assert mem._approx_tokens() <= 60


def test_truncation_never_leaves_orphan_tool_message():
    """回归: 旧实现丢 assistant 却把它的 tool 结果留下, 首条变成 role=tool → 上游 400。"""
    mem = Memory(max_messages=10**6, max_tokens=90)
    tool_pair(mem)
    for i in range(6):
        mem.add_user(f"后来的消息 {i} " + "长" * 20)
    roles = [m.role for m in mem.messages()]
    assert roles[:1] != ["tool"]
    for i, m in enumerate(mem.messages()):
        if m.role == "tool":
            assert any(
                a.role == "assistant" and a.tool_calls
                and any(tc["id"] == m.tool_call_id for tc in a.tool_calls)
                for a in mem.messages()[:i]
            ), f"第 {i} 条 tool 消息没有对应 assistant: {roles}"


def test_message_cap_slice_keeps_tool_pairing():
    mem = Memory(max_messages=3, max_tokens=10**9)
    tool_pair(mem)
    mem.add_user("问题一")
    mem.add_user("问题二")
    mem.add_user("问题三")
    roles = [m.role for m in mem.messages()]
    assert roles[0] != "tool", roles


def test_single_oversized_group_does_not_hang():
    mem = Memory(max_messages=10**6, max_tokens=10)
    tool_pair(mem)
    assert len(mem.messages()) >= 1


def test_estimate_tokens():
    assert Message.estimate_tokens("") == 0
    assert Message.estimate_tokens("abc") >= 1
    assert Message.estimate_tokens("a" * 300) > Message.estimate_tokens("a" * 100)


def test_clear_and_to_openai_shape():
    mem = Memory()
    mem.add_user("你好")
    mem.add_assistant("在的", tool_calls=[{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}])
    mem.add_tool("结果", tool_call_id="c1")
    d = mem.to_openai_list()
    assert d[0] == {"role": "user", "content": "你好"}
    assert d[1]["tool_calls"][0]["id"] == "c1"
    assert d[2] == {"role": "tool", "content": "结果", "tool_call_id": "c1"}
    mem.clear()
    assert mem.messages() == []


# ---------- SQLite ----------
def test_store_creates_parent_dirs(store):
    assert store.db_path.is_file()


def test_save_and_load_roundtrip(store):
    mem = Memory()
    mem.add_user("你好 中文")
    tool_pair(mem)
    store.save("s1", mem)
    back = store.load("s1")
    assert [m.to_openai() for m in back.messages()] == [m.to_openai() for m in mem.messages()]
    assert back.messages()[1].tool_calls[0]["function"]["name"] == "get_current_time"
    assert json.loads(back.messages()[1].tool_calls[0]["function"]["arguments"]) == {}


def test_load_missing_session_returns_none(store):
    assert store.load("ghost") is None


def test_save_overwrites_not_appends(store):
    mem = Memory()
    mem.add_user("第一轮")
    store.save("s1", mem)
    mem.add_user("第二轮")
    store.save("s1", mem)
    assert len(store.load("s1").messages()) == 2, "覆盖式保存, 不是追加"


def test_list_and_get_session(store):
    mem = Memory()
    mem.add_user("x")
    store.save("alpha", mem)
    store.save("beta", mem)
    ids = {s["session_id"] for s in store.list_sessions()}
    assert ids == {"alpha", "beta"}
    assert store.get_session("alpha")["msg_count"] == 1
    assert store.get_session("nope") is None


@pytest.mark.xfail(reason="已知缺陷: 标题一旦以 auto- 开头, memory_store.save 就不再接受显式标题", strict=True)
def test_explicit_title_overrides_auto_title(store):
    mem = Memory()
    mem.add_user("x")
    store.save("s", mem, title="auto-s")
    store.save("s", mem, title="用户起的名字")
    assert store.get_session("s")["title"] == "用户起的名字"


def test_delete_session_cascades_messages(store):
    mem = Memory()
    mem.add_user("x")
    store.save("s1", mem)
    assert store.delete_session("s1") is True
    assert store.load("s1") is None
    conn = store._connect()
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='s1'").fetchone()[0] == 0
    assert store.delete_session("s1") is False


def test_store_survives_reopen(tmp_path):
    from agent import MemoryStore

    path = tmp_path / "reopen.db"
    m = Memory()
    m.add_user("重启前说的话")
    MemoryStore(path).save("s", m)
    assert [x.content for x in MemoryStore(path).load("s").messages()] == ["重启前说的话"]
