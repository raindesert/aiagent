"""Web UI: ui_support 的纯逻辑离线测, ui.py 用 streamlit 的 AppTest 跑真实渲染。

AppTest 会重新执行整个脚本, 所以没法用 monkeypatch 换掉模型: 发消息的用例把
配置指向 tests/fakes.py 里的本地假 OpenAI 服务, 真模型那条额外标 e2e。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent import AgentResult, Memory, Message
from agent.ui_support import history_view, stream_turn

REPO = Path(__file__).resolve().parents[1]


# ---------------- history_view ----------------
def test_history_view_skips_system_and_keeps_chat_roles():
    mem = Memory()
    mem.add("system", "你是助手")
    mem.add_user("几点了")
    mem.add_assistant("现在 10 点")
    assert history_view(mem.messages()) == [
        {"kind": "chat", "role": "user", "content": "几点了"},
        {"kind": "chat", "role": "assistant", "content": "现在 10 点"},
    ]


def test_history_view_merges_tool_call_with_its_result():
    mem = Memory()
    mem.add_user("查时间")
    mem.add_assistant(
        "",
        tool_calls=[{
            "id": "c1",
            "type": "function",
            "function": {"name": "get_current_time", "arguments": '{"timezone":"Asia/Shanghai"}'},
        }],
    )
    mem.add_tool("2026-09-24T10:00:00+08:00", tool_call_id="c1")
    mem.add_assistant("10 点")

    items = history_view(mem.messages())
    assert [i["kind"] for i in items] == ["chat", "tool", "chat"]
    tool = items[1]
    assert tool["name"] == "get_current_time"
    # 参数 JSON 重排成紧凑单行, 便于放进 expander 标题
    assert tool["arguments"] == '{"timezone": "Asia/Shanghai"}'
    assert tool["result"] == "2026-09-24T10:00:00+08:00"


def test_history_view_pairs_results_by_tool_call_id():
    # 一次 assistant 发多个 tool_calls, 结果按 id 对号入座, 不按到达顺序猜
    mem = Memory()
    mem.add_assistant(None, tool_calls=[
        {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"b.txt"}'}},
        {"id": "b", "type": "function", "function": {"name": "grep_search", "arguments": '{"pattern":"x"}'}},
    ])
    mem.add_tool("第二个结果", tool_call_id="b")
    mem.add_tool("第一个结果", tool_call_id="a")

    tools = [i for i in history_view(mem.messages()) if i["kind"] == "tool"]
    assert [(t["name"], t["result"]) for t in tools] == [
        ("read_file", "第一个结果"),
        ("grep_search", "第二个结果"),
    ]


def test_history_view_keeps_assistant_text_before_tool_call():
    mem = Memory()
    mem.add_assistant(
        "先查一下",
        tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "get_current_time", "arguments": "{}"},
        }],
    )
    mem.add_tool("结果", tool_call_id="c1")
    items = history_view(mem.messages())
    # 助手先说话, 紧跟它的工具调用才显示
    assert [i["kind"] for i in items] == ["chat", "tool"]
    assert items[0]["content"] == "先查一下"
    assert items[1]["result"] == "结果"
    assert items[1]["arguments"] == "{}"


def test_history_view_shows_orphan_tool_result_without_parent():
    # 截断把父 assistant 丢掉后, tool 结果仍要显示出来而不是消失
    items = history_view([Message(role="tool", content="孤立结果", tool_call_id="cX")])
    assert items == [{"kind": "tool", "name": "?", "arguments": "", "result": "孤立结果"}]


def test_history_view_truncates_long_tool_result():
    items = history_view([
        Message(role="assistant", content=None, tool_calls=[
            {"id": "c", "type": "function", "function": {"name": "grep_search", "arguments": "{}"}}]),
        Message(role="tool", content="x" * 3000, tool_call_id="c"),
    ])
    assert "截断" in items[0]["result"]
    assert len(items[0]["result"]) < 1200


# ---------------- stream_turn ----------------
class StubAgent:
    def __init__(self, error=None, stream=True):
        self.error = error
        self.stream = stream
        self.calls: list[str] = []

    def chat(self, text, on_token=None):
        self.calls.append(text)
        if self.error:
            raise self.error
        if self.stream:
            for piece in ["你", "好"]:
                on_token(piece)
        return AgentResult(content="你好", iterations=2, tool_calls_made=["get_current_time"])


def collect(agent, text="在吗"):
    return list(stream_turn(agent, text))


def test_stream_turn_yields_full_text_when_model_is_not_streaming():
    # 模型没开 stream 时压根没有 token 回调, 结束时一次性补全文
    events = collect(StubAgent(stream=False))
    assert [k for k, _ in events] == ["token", "result"]
    assert events[0][1] == "你好"
    assert events[-1][1].iterations == 2


def test_stream_turn_passes_tokens_through_then_result():
    events = collect(StubAgent())
    assert [k for k, _ in events] == ["token", "token", "result"]
    assert "".join(p for k, p in events if k == "token") == "你好"


def test_stream_turn_calls_chat_exactly_once():
    agent = StubAgent()
    collect(agent)
    assert agent.calls == ["在吗"]


def test_stream_turn_propagates_agent_error():
    with pytest.raises(RuntimeError, match="模型挂了"):
        collect(StubAgent(error=RuntimeError("模型挂了")))


def test_stream_turn_keeps_partial_tokens_before_error():
    class HalfStream(StubAgent):
        def chat(self, text, on_token=None):
            on_token("前半")
            raise RuntimeError("断了")

    seen = []
    with pytest.raises(RuntimeError):
        for kind, payload in stream_turn(HalfStream(), "在吗"):
            if kind == "token":
                seen.append(payload)
    assert seen == ["前半"]


# ---------------- ui.py 渲染 ----------------
@pytest.fixture
def app(monkeypatch, tmp_path):
    """隔离 HOME (避免碰用户真实的 ~/.aiagent/memory.db) + 清掉 streamlit 全局缓存。"""
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    streamlit.cache_resource.clear()
    return AppTest.from_file(str(REPO / "ui.py"), default_timeout=60)


def test_app_renders_sidebar_and_history_without_model_call(app):
    app.run()
    assert app.exception == []
    assert [c.label for c in app.sidebar.checkbox] == ["显示每轮 iter/工具统计"]
    backend = app.sidebar.selectbox[0]
    assert backend.value == "local"
    # 模型行显示的是 models.yaml 里的真实 model 名
    assert any("CPM5-2B" in c.value for c in app.caption)
    expander_labels = [e.label for e in app.expander]
    assert any(label.startswith("工具 (") for label in expander_labels)


def test_app_reports_bad_config_instead_of_crashing(app):
    app.run()
    app.sidebar.text_input[0].set_value("does_not_exist.yaml")
    app.run()
    assert app.exception == []
    assert any("找不到配置文件" in e.value for e in app.error)


def test_app_new_session_button_resets_history(app):
    app.run()
    assert [s.value for s in app.sidebar.selectbox][1] == "default"
    app.sidebar.button[0].set_value(True)
    app.run()
    assert app.exception == []
    current = [s.value for s in app.sidebar.selectbox][1]
    assert current != "default"
    assert any(f"session: `{current}`" in c.value for c in app.caption)


# ---------------- 真发一条消息: 假 OpenAI 服务当后端 ----------------
MODELS_UI_YAML = """\
default: fake
models:
  - name: fake
    backend: openai
    model: fake
    base_url: "{base_url}"
    api_key: fake-key
    temperature: 0.1
    stream: true
"""

AGENT_UI_YAML = """\
name: ui-test
models_file: "{models}"
default_model: fake
context:
  system_prompt:
    template: "你是 {{role}}"
    variables:
      role: 测试助手
tools:
  - name: get_current_time
    description: 获取当前时间
    enabled: true
    parameters:
      type: object
      properties: {{}}
      required: []
    handler: tools.time:get_current_time
"""


@pytest.fixture
def fake_app(app, tmp_path):
    """app 的侧边栏配置指向临时 yaml, 后端是 127.0.0.1 上的假 OpenAI。"""
    from fakes import FakeOpenAI

    fake = FakeOpenAI()
    models_path = tmp_path / "models_ui.yaml"
    models_path.write_text(
        MODELS_UI_YAML.format(base_url=fake.base_url), encoding="utf-8"
    )
    agent_path = tmp_path / "agent_ui.yaml"
    agent_path.write_text(
        AGENT_UI_YAML.format(models=models_path.as_posix()), encoding="utf-8"
    )

    app.run()
    app.sidebar.text_input[0].set_value(str(agent_path))
    app.run()
    assert app.exception == []
    assert [t.value for t in app.title] == ["ui-test"]
    yield app, fake
    fake.stop()


def test_app_sends_message_and_renders_streamed_answer(fake_app):
    app, fake = fake_app
    app.chat_input[0].set_value("现在几点")
    app.run()

    assert app.exception == []
    assert app.error == []
    bubbles = [m.value for m in app.main.markdown]
    assert "现在几点" in bubbles
    assert fake.TOOL_TEXT + fake.FINAL_TEXT in bubbles
    # 模型先调工具再回答, 所以打了两次后端
    assert len(fake.requests) == 2


def test_app_collapses_tool_call_into_expander(fake_app):
    app, fake = fake_app
    app.chat_input[0].set_value("现在几点")
    app.run()

    def panels():
        return [e for e in app.expander if e.label.startswith("工具 get_current_time")]

    assert len(panels()) == 1
    assert any("20" in c.value for c in panels()[0].code)  # 时间工具的返回

    # 下一次 rerun 走历史渲染, 不能重复画同一条工具记录
    app.run()
    assert len(panels()) == 1


def test_app_stats_caption_only_when_enabled(fake_app):
    app, fake = fake_app
    app.sidebar.checkbox[0].set_value(True)
    app.run()
    app.chat_input[0].set_value("现在几点")
    app.run()

    assert any(c.value.startswith("iter=2") for c in app.caption)


def test_app_slash_input_is_not_treated_as_command(fake_app):
    app, fake = fake_app
    app.chat_input[0].set_value("/reset")
    app.run()
    assert app.exception == []
    assert any("不解析斜杠命令" in i.value for i in app.info)
    # 关键: 没打到后端, 也没被当成命令执行
    assert fake.requests == []


# ---------------- 真模型端到端 ----------------
@pytest.mark.e2e
def test_app_streams_answer_from_local_model(app, local_model):
    app.run()
    app.chat_input[0].set_value("只回复两个字:你好")
    app.run()
    assert app.exception == []
    assert not any("这轮失败" in e.value for e in app.error)
    bubbles = [m.value for m in app.main.markdown]
    assert any("只回复两个字" in b for b in bubbles)
    assert any("你好" in b for b in bubbles)
    # 默认不显示 iter 统计
    assert not any(c.value.startswith("iter=") for c in app.caption)
