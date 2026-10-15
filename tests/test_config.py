"""配置加载 / ${ENV} 展开 / system prompt 渲染 / 模型客户端构造。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent import (
    ModelClient,
    ModelConfig,
    entry_to_model_config,
    find_model_entry,
    load_config,
)
from agent.config import AgentConfig, _expand_env
from agent.context import ContextManager


# ---------- ${ENV} 展开 ----------
def test_env_var_expanded(monkeypatch):
    monkeypatch.setenv("AIAGENT_PROBE_KEY", "sk-from-env")
    assert _expand_env("${AIAGENT_PROBE_KEY}") == "sk-from-env"


def test_env_partial_expansion(monkeypatch):
    monkeypatch.setenv("AIAGENT_PROBE_KEY", "V")
    assert _expand_env({"a": ["x${AIAGENT_PROBE_KEY}y"], "b": 5}) == {"a": ["xVy"], "b": 5}


def test_missing_env_var_keeps_literal(monkeypatch):
    monkeypatch.delenv("AIAGENT_NOT_SET", raising=False)
    assert _expand_env("${AIAGENT_NOT_SET}") == "${AIAGENT_NOT_SET}"


# ---------- agent.yaml / models.yaml ----------
def test_agent_yaml_loads(cfg):
    assert cfg.models_file.endswith("models.yaml")
    assert cfg.default_model in {"local", "local2", "deepseek", "modelscope", ""}
    assert cfg.context.max_history_messages > 0
    assert cfg.loop.max_iterations > 0


def test_all_declared_tools_have_importable_handlers(cfg):
    reg_names = set(__import__("agent").ToolRegistry(cfg.tools).names())
    enabled = {t.name for t in cfg.tools if t.enabled}
    assert enabled == reg_names, "有工具的 handler 导入失败"
    assert len(enabled) == len(cfg.tools) >= 10


def test_tool_names_unique(cfg):
    names = [t.name for t in cfg.tools]
    assert len(names) == len(set(names))
    handlers = [t.handler for t in cfg.tools]
    assert len(handlers) == len(set(handlers))


def test_every_schema_is_valid_json_schema(cfg):
    for t in cfg.tools:
        assert t.parameters["type"] == "object"
        props = t.parameters["properties"]
        for required in t.parameters.get("required", []):
            assert required in props, f"{t.name} 的必填参数 {required} 没在 properties 里"


@pytest.mark.xfail(reason="已知缺陷: AgentConfig 是扁平 schema, agent.yaml 的 `agent:` 块被静默忽略", strict=True)
def test_agent_yaml_name_takes_effect(cfg):
    assert cfg.name == "my-assistant"


# ---------- 校验 ----------
def test_context_is_required():
    with pytest.raises(Exception):
        AgentConfig.model_validate({})


def test_system_prompt_template_required():
    with pytest.raises(Exception):
        AgentConfig.model_validate({"context": {"system_prompt": {}}})


# ---------- system prompt ----------
def test_system_prompt_fills_variables(cfg):
    sp = ContextManager(cfg.context).build_system_prompt()
    assert "编程助手" in sp
    assert "{" not in sp, "模板变量没被替换干净"


def test_system_prompt_injects_current_time(cfg):
    import re

    sp = ContextManager(cfg.context).build_system_prompt()
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", sp)


def test_runtime_variables_override_config(cfg):
    sp = ContextManager(cfg.context).build_system_prompt({"role": "运维助手"})
    assert "运维助手" in sp and "编程助手" not in sp


def test_undefined_template_variable_falls_back(cfg):
    ctx = cfg.context.model_copy(deep=True)
    ctx.system_prompt.template = "值 {nope_var}"
    assert "<missing:nope_var>" in ContextManager(ctx).build_system_prompt()


def test_literal_braces_in_template_survive(cfg):
    """提示词里常要写 JSON 示例, 确认这些花括号原样保留, 且其它变量照样被替换。"""
    ctx = cfg.context.model_copy(deep=True)
    ctx.system_prompt.template = '返回 {"ok": true} 给 {role}, 集合 {} 与 {0}'
    sp = ContextManager(ctx).build_system_prompt()
    assert '{"ok": true}' in sp
    assert "编程助手" in sp, "回归: 之前遇到 JSON 示例就整个模板都不替换了"
    assert "{role}" not in sp
    assert "{}" in sp and "{0}" in sp


def test_models_file_resolved_relative_to_config_file(repo_root, tmp_path, monkeypatch):
    """回归: models_file 之前按 cwd 解析, `-c 别的目录/agent.yaml` 会 FileNotFoundError。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "agent.yaml").write_text((repo_root / "agent.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (proj / "models.yaml").write_text((repo_root / "models.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(proj / "agent.yaml")
    assert Path(cfg.models_file).parent == proj
    assert Path(cfg.models_file).is_file()


def test_agent_constructs_from_any_cwd(repo_root, tmp_path, monkeypatch):
    """同一个场景走到 Agent 构造: 不该因为 cwd 不同就 traceback。"""
    from agent import Agent

    proj = tmp_path / "proj2"
    proj.mkdir()
    (proj / "agent.yaml").write_text((repo_root / "agent.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (proj / "models.yaml").write_text((repo_root / "models.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere2"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    a = Agent(load_config(proj / "agent.yaml"), session_id="cwd-test")
    assert a.current_model_name == "local"


def test_missing_models_file_raises_clear_error(repo_root, tmp_path, monkeypatch):
    from agent import Agent

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "agent.yaml").write_text((repo_root / "agent.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(bare / "agent.yaml")  # models.yaml 不存在
    with pytest.raises(FileNotFoundError) as e:
        Agent(cfg, session_id="x")
    assert "模型配置文件不存在" in str(e.value) and "models.yaml" in str(e.value)


def test_build_messages_puts_system_first(cfg, store):
    from agent import Memory

    mem = Memory()
    mem.add_user("你好")
    msgs = ContextManager(cfg.context).build_messages(mem)
    assert [m["role"] for m in msgs] == ["system", "user"]


# ---------- 模型条目 / 客户端 ----------
def test_find_model_entry_by_name(models_cfg):
    assert find_model_entry(models_cfg, models_cfg.models[0].name).name == models_cfg.models[0].name
    assert find_model_entry(models_cfg).name == models_cfg.default


def test_find_model_entry_unknown_lists_available(models_cfg):
    with pytest.raises(KeyError) as e:
        find_model_entry(models_cfg, "definitely-not-here")
    assert "可用" in str(e.value)


def test_entry_to_model_config_maps_upstream_model(models_cfg):
    entry = models_cfg.models[0]
    mc = entry_to_model_config(entry)
    assert mc.name == entry.model and mc.base_url == entry.base_url


def test_empty_api_key_rejected():
    with pytest.raises(ValueError):
        ModelClient(ModelConfig(name="m", api_key=""))


@pytest.mark.xfail(reason="已知缺陷: env 没设时 api_key 留成字面量 '${...}', 空值守卫不触发", strict=True)
def test_unset_env_var_should_not_pass_as_api_key(models_cfg, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    entry = find_model_entry(models_cfg, "deepseek")
    with pytest.raises(ValueError):
        ModelClient(entry_to_model_config(entry))
