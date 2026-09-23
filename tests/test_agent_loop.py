"""Agent 主循环 / 模型切换 / session 管理 —— 用假模型, 全程离线。"""
from __future__ import annotations

import copy
import re

import pytest

from agent import Agent, MemoryStore
from fakes import attach


@pytest.fixture
def agent(cfg, store):
    """深拷贝配置, 免得改坏 session 级的共享 cfg。"""
    a = Agent(copy.deepcopy(cfg), store=store, session_id="t")
    a.cfg.loop.max_iterations = 4
    return a


# ---------- 主循环 ----------
def test_plain_text_answer_stops_loop(agent):
    attach(agent, ["最终答案"])
    r = agent.chat("你好")
    assert (r.content, r.iterations, r.tool_calls_made) == ("最终答案", 1, [])


def test_tool_call_then_answer(agent):
    attach(agent, ["tool", "答案"])
    r = agent.chat("现在几点")
    assert r.iterations == 2 and r.tool_calls_made == ["get_current_time"]
    assert [m.role for m in agent.memory.messages()] == ["user", "assistant", "tool", "assistant"]


def test_tool_result_is_real_and_fed_back(agent):
    attach(agent, ["tool", "答案"])
    agent.chat("现在几点")
    tool_msg = next(m for m in agent.memory.messages() if m.role == "tool")
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", tool_msg.content)


def test_assistant_tool_call_message_keeps_tool_calls_for_next_turn(agent):
    attach(agent, ["tool", "答案"])
    agent.chat("几点")
    second_request = agent.model.seen_roles[-1]
    assert second_request == ["system", "user", "assistant", "tool"], "第二轮要带上 assistant(tool_calls) 和 tool 结果"


def test_multi_round_tool_calls(agent):
    attach(agent, ["tool"] * 3 + ["答案"])
    r = agent.chat("反复查")
    assert r.iterations == 4 and len(r.tool_calls_made) == 3


def test_max_iterations_breaks_infinite_tool_loop(agent):
    attach(agent, ["tool"] * 50)
    r = agent.chat("死循环")
    assert r.iterations == agent.cfg.loop.max_iterations
    assert "最大迭代次数" in r.content


def test_unknown_tool_error_returned_to_model_instead_of_crashing(agent):
    attach(agent, ["badtool", "答案"])
    r = agent.chat("调不存在的工具")
    fed = next(m for m in agent.memory.messages() if m.role == "tool")
    assert "未知工具" in fed.content
    assert r.content == "答案"


def test_streaming_callback_receives_tokens(cfg, store):
    a = Agent(cfg, store=store, session_id="stream")
    fake = attach(a, ["流式内容"])
    a.model.cfg.stream = True
    seen = []
    a.chat("说句话", on_token=lambda t: seen.append(t))
    assert "".join(seen) == "流式内容"


# ---------- 持久化联动 ----------
def test_history_auto_saved_after_chat(agent, store):
    attach(agent, ["答案"])
    agent.chat("说点什么")
    assert len(store.load("t").messages()) == 2


def test_auto_save_can_be_disabled(agent, store):
    agent.auto_save = False
    attach(agent, ["答案"])
    agent.chat("不落盘")
    assert store.load("t") is None


def test_history_reloaded_on_new_agent(cfg, store):
    a1 = Agent(cfg, store=store, session_id="persist")
    attach(a1, ["回答一"])
    a1.chat("问题一")
    a2 = Agent(cfg, store=store, session_id="persist")
    assert [m.content for m in a2.memory.messages()] == ["问题一", "回答一"]


def test_works_without_store(cfg):
    a = Agent(cfg, session_id="x")
    attach(a, ["答案"])
    assert a.chat("没有 store").content == "答案"
    a.save()  # 不该抛异常
    assert a.list_sessions() == []


# ---------- session 切换 ----------
def test_new_session_isolates_history(cfg, store):
    a = Agent(cfg, store=store, session_id="A")
    attach(a, ["A 的回答"])
    a.chat("A 的问题")
    a.new_session("B")
    assert a.memory.messages() == []
    assert {s["session_id"] for s in a.list_sessions()} >= {"A"}


def test_switch_session_loads_target(cfg, store):
    a = Agent(cfg, store=store, session_id="A")
    attach(a, ["A 的回答"])
    a.chat("A 的问题")
    a.new_session("B")
    attach(a, ["B 的回答"])
    a.chat("B 的问题")
    assert "已切换" in a.switch_session("A")
    assert [m.content for m in a.memory.messages()] == ["A 的问题", "A 的回答"]
    assert "开空 session" in a.switch_session("never-used")
    assert a.memory.messages() == []
    assert "无需切换" in a.switch_session("never-used")


def test_reset_clears_memory_and_store(cfg, store):
    a = Agent(cfg, store=store, session_id="r")
    attach(a, ["答案"])
    a.chat("问题")
    a.reset()
    assert a.memory.messages() == []
    assert store.load("r") is None or store.load("r").messages() == []


# ---------- 模型切换 ----------
def test_default_model_comes_from_config(cfg, store):
    a = Agent(cfg, store=store)
    assert a.current_model_name == (cfg.default_model or a._models_cfg.default)


def test_switch_model_roundtrip_keeps_history(cfg, store):
    a = Agent(cfg, store=store, session_id="m")
    attach(a, ["答案"])
    a.chat("问题")
    other = next(n for n in a.list_models() if n != a.current_model_name)
    assert "已切换" in a.switch_model(other)
    assert a.current_model_name == other
    assert "无需切换" in a.switch_model(other)
    assert len(a.memory.messages()) == 2


def test_switch_model_unknown_raises_keyerror(cfg, store):
    a = Agent(cfg, store=store)
    with pytest.raises(KeyError):
        a.switch_model("not-a-model")


def test_model_info_marks_exactly_one_current(cfg, store):
    a = Agent(cfg, store=store)
    infos = a.model_info()
    assert sum(1 for i in infos if i["current"]) == 1
    assert {"name", "model", "base_url", "current"} <= set(infos[0])
