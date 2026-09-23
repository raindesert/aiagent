"""端到端: 真模型 + 真工具 + 真 SQLite。需要 models.yaml 默认后端在线, 否则整体 skip。

跑法: pytest -m e2e
"""
from __future__ import annotations

import re
import time

import pytest

from agent import Agent, MemoryStore

pytestmark = pytest.mark.e2e


@pytest.fixture
def e2e_store(tmp_path):
    return MemoryStore(tmp_path / "e2e.db")


@pytest.fixture
def agent(safe_cfg, e2e_store, local_model):
    a = Agent(safe_cfg, store=e2e_store, session_id="e2e")
    assert a.model.cfg.api_key, "模型 key 未配置"
    return a


def test_plain_answer_streams_tokens(agent):
    seen: list[str] = []
    t0 = time.time()
    r = agent.chat("用一句话说你好", on_token=seen.append)
    assert r.content.strip(), "模型没返回文本"
    assert time.time() - t0 < 180
    assert seen, "stream=true 时应该逐 token 回调"
    assert "".join(seen) == r.content
    assert r.iterations == 1


def test_tool_chain_with_real_model(agent):
    r = agent.chat("现在几点了? 必须调用 get_current_time 工具查, 不要自己猜", on_token=lambda t: None)
    assert r.content
    if "get_current_time" not in r.tool_calls_made:
        pytest.skip(f"小模型没选择调工具 (iter={r.iterations}), 工具链路另有离线用例")
    tool_msg = next(m for m in agent.memory.messages() if m.role == "tool")
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", tool_msg.content)
    assert any(m.tool_calls for m in agent.memory.messages())


def test_non_streaming_path(agent):
    agent.model.cfg.stream = False
    r = agent.chat("只回复两个字: 收到")
    assert r.content.strip()


def test_history_persisted_and_reloaded(safe_cfg, e2e_store, local_model):
    a = Agent(safe_cfg, store=e2e_store, session_id="reload")
    a.model.cfg.stream = False
    first = a.chat("只回复一个字: 好")
    assert first.content
    b = Agent(safe_cfg, store=e2e_store, session_id="reload")
    assert [m.role for m in b.memory.messages()] == ["user", "assistant"]


def test_multi_turn_context(agent):
    agent.model.cfg.stream = False
    agent.chat("我叫小明, 只需要回复 OK")
    r = agent.chat("我叫什么名字? 直接回答, 不要用工具")
    assert "小明" in r.content, f"上下文没带上名字: {r.content!r}"
