"""CLI 交互层: 斜杠命令 + "每条输入只调用一次 chat" 的回归测试。

真模型和真 SQLite 都用替身挡住, 这些用例完全离线。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent as agent_pkg
import cli
from agent import AgentConfig, AgentResult, ModelConfig, load_config, load_models_config

REPO = Path(__file__).resolve().parents[1]


class FakeMemory:
    def __init__(self):
        self.items: list = []

    def messages(self):
        return self.items

    def clear(self):
        self.items.clear()


class FakeTools:
    @staticmethod
    def names():
        return ["get_current_time", "read_file"]


class FakeModel:
    def __init__(self):
        self.cfg = ModelConfig(name="fake", api_key="k", base_url="http://x/v1", stream=True)


class FakeAgent:
    """只实现 cli.main() 用到的接口, 顺便记录每次调用。"""

    last: "FakeAgent | None" = None

    def __init__(self, cfg: AgentConfig, store=None, session_id="default", **kw):
        self.cfg = cfg
        self.session_id = session_id
        self.chat_calls: list[str] = []
        self.model = FakeModel()
        self.tools = FakeTools()
        self.memory = FakeMemory()
        self.models = load_models_config(cfg.models_file)
        FakeAgent.last = self

    def chat(self, text, on_token=None):
        self.chat_calls.append(text)
        if on_token and self.model.cfg.stream:
            on_token("答")
            on_token("案")
        return AgentResult(content="答案", iterations=1, tool_calls_made=[])

    @property
    def current_model_name(self):
        return self.models.default

    def reset(self):
        self.memory.clear()

    def switch_model(self, name):
        if name not in [m.name for m in self.models.models]:
            raise KeyError(f"模型 {name!r} 不在配置里")
        return f"已切换: {name}"

    def model_info(self):
        return [{"name": m.name, "model": m.model, "base_url": m.base_url,
                 "current": m.name == self.models.default} for m in self.models.models]

    def list_sessions(self):
        return [{"session_id": "s1", "msg_count": 3, "updated_at": "t", "title": "s1"}]

    def new_session(self, sid=None, title=None):
        self.session_id = sid or "new-1"
        return "已开新 session"

    def switch_session(self, sid):
        self.session_id = sid
        return "已切换 session"

    def save(self, title=None):
        return None

    def tools_names(self):
        return ["get_current_time", "read_file"]


class FakeStore:
    def __init__(self, path=""):
        self.path = path

    def load(self, *a, **kw):
        return None

    def save(self, *a, **kw):
        return None


@pytest.fixture
def agent_cfg():
    cfg = load_config(REPO / "agent.yaml")
    cfg.models_file = str(REPO / "models.yaml")
    return cfg


@pytest.fixture
def cli_env(monkeypatch, agent_cfg):
    created: list[FakeAgent] = []

    def make(cfg, **kw):
        inst = FakeAgent(cfg, **kw)
        created.append(inst)
        return inst

    monkeypatch.setattr(agent_pkg, "MemoryStore", FakeStore)
    monkeypatch.setattr(cli, "Agent", make)
    monkeypatch.setattr(cli, "load_config", lambda p: agent_cfg)
    return created


def feed(monkeypatch, lines):
    it = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(it).strip()
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)


def run_cli(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["cli.py", *argv])
    return cli.main()


# ---------- 核心回归 ----------
def test_interactive_mode_calls_chat_once_per_input(cli_env, monkeypatch, capsys):
    """回归: 对话代码块曾被整段复制两遍 → 一条输入两次模型调用 + 两次工具执行。"""
    feed(monkeypatch, ["第一句", "第二句", "/quit"])
    assert run_cli(monkeypatch, []) == 0
    assert cli_env[0].chat_calls == ["第一句", "第二句"]
    assert "[iter=" not in capsys.readouterr().out, "默认不该显示轮次统计行"


def test_single_shot_message_mode_prints_answer(cli_env, monkeypatch, capsys):
    assert run_cli(monkeypatch, ["-m", "说句话"]) == 0
    assert cli_env[0].chat_calls == ["说句话"]
    out = capsys.readouterr().out
    assert "答案" in out, "单轮模式要把回答打到 stdout"
    assert "[iter=" not in out


def test_banner_hidden_by_default(cli_env, monkeypatch, capsys):
    feed(monkeypatch, ["/quit"])
    assert run_cli(monkeypatch, []) == 0
    out = capsys.readouterr().out
    assert "=====" not in out and "tools:" not in out, "默认不打印 banner"


@pytest.mark.parametrize("flag", ["-v", "--debug"])
def test_banner_shown_with_verbose(cli_env, monkeypatch, capsys, flag):
    feed(monkeypatch, ["/quit"])
    assert run_cli(monkeypatch, [flag]) == 0
    out = capsys.readouterr().out
    assert "=====" in out and "tools:" in out


@pytest.mark.parametrize("flag", ["-v", "--debug"])
def test_iteration_stats_only_with_verbose(cli_env, monkeypatch, capsys, flag):
    """轮次统计行 ([iter=N tools=[...]]) 归 -v 管, 默认不打扰正常对话。"""
    feed(monkeypatch, ["第一句", "第二句", "/quit"])
    assert run_cli(monkeypatch, [flag]) == 0
    out = capsys.readouterr().out
    assert out.count("[iter=1 tools=[]]") == 2, "每条输入只应打印一次状态行"


def test_iteration_stats_in_message_mode_with_verbose(cli_env, monkeypatch, capsys):
    assert run_cli(monkeypatch, ["-v", "-m", "说句话"]) == 0
    assert "[iter=1 tools=[]]" in capsys.readouterr().out


def test_interactive_passes_streaming_callback(cli_env, monkeypatch, capsys):
    feed(monkeypatch, ["说话", "/quit"])
    run_cli(monkeypatch, [])
    err = capsys.readouterr().err
    assert "答案" in err, "流式 token 应该打到 stderr"
    assert err.endswith("\n"), "流式结束后要补换行, 否则和 stdout 串行错乱"


# ---------- 斜杠命令 ----------
def test_slash_commands_do_not_reach_model(cli_env, monkeypatch, capsys):
    feed(monkeypatch, ["/tools", "/models", "/model", "/quit"])
    run_cli(monkeypatch, [])
    out = capsys.readouterr().out
    assert " - get_current_time" in out
    assert "*" in out and "local" in out
    assert "当前模型:" in out
    assert cli_env[0].chat_calls == []


def test_model_switch_failure_is_caught(cli_env, monkeypatch, capsys):
    feed(monkeypatch, ["/model not-a-model", "/quit"])
    run_cli(monkeypatch, [])
    assert "切换失败" in capsys.readouterr().out


def test_session_subcommands(cli_env, monkeypatch, capsys):
    feed(monkeypatch, ["/session", "/session list", "/session new p", "/session switch p",
                       "/session save 标题", "/session bogus", "/session switch", "/quit"])
    run_cli(monkeypatch, [])
    out = capsys.readouterr().out
    for expected in ["当前 session:", "s1", "已开新 session", "已切换 session", "已保存",
                     "未知子命令", "用法: /session switch <id>"]:
        assert expected in out, expected


def test_reset_and_empty_input_and_quit(cli_env, monkeypatch, capsys):
    feed(monkeypatch, ["/reset", "", "   ", ":q"])
    assert run_cli(monkeypatch, []) == 0
    assert "已清空" in capsys.readouterr().out
    assert cli_env[0].chat_calls == []


def test_eof_exits_cleanly(cli_env, monkeypatch):
    feed(monkeypatch, [])
    assert run_cli(monkeypatch, []) == 0


@pytest.mark.parametrize("cmd", ["/exit", "/quit", ":q"])
def test_quit_aliases(cli_env, monkeypatch, cmd):
    feed(monkeypatch, [cmd])
    assert run_cli(monkeypatch, []) == 0


def test_unknown_slash_command_goes_to_model(cli_env, monkeypatch):
    """已知行为: 只有内置前缀被识别, 其余 (哪怕长得像命令) 直接进对话。"""
    feed(monkeypatch, ["/badcmd", "/quit"])
    run_cli(monkeypatch, [])
    assert cli_env[0].chat_calls == ["/badcmd"]


# ---------- 启动参数 ----------
def test_missing_config_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cli.py", "-c", "no_such_file.yaml"])
    assert cli.main() == 2
    assert "找不到配置文件" in capsys.readouterr().err


def test_print_config_mode(monkeypatch, capsys, agent_cfg):
    monkeypatch.setattr(cli, "load_config", lambda p: agent_cfg)
    monkeypatch.setattr("sys.argv", ["cli.py", "--print-config"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert '"tools"' in out and "get_current_time" in out


@pytest.mark.parametrize("flag", ["-v", "--debug", "-q"])
def test_log_level_flags_accepted(cli_env, monkeypatch, flag):
    feed(monkeypatch, ["/quit"])
    assert run_cli(monkeypatch, [flag]) == 0
