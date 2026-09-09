"""Pydantic 配置模型 + YAML 加载。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """递归展开字符串里的 ${ENV_VAR}。"""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class SystemPromptConfig(BaseModel):
    template: str
    variables: dict[str, str] = Field(default_factory=dict)


class ContextConfig(BaseModel):
    system_prompt: SystemPromptConfig
    max_history_messages: int = 30
    max_history_tokens: int = 6000


class ModelConfig(BaseModel):
    backend: str = "openai"
    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: int = 60
    stream: bool = True

    @field_validator("api_key")
    @classmethod
    def _warn_empty_key(cls, v: str) -> str:
        if not v:
            # 允许为空, 真正调用时会再报错
            pass
        return v


class ModelEntry(BaseModel):
    """models.yaml 里一个模型后端的配置。"""
    name: str
    backend: str = "openai"
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: int = 60
    stream: bool = True


class ModelsConfig(BaseModel):
    """整个 models.yaml。"""
    default: str
    models: list[ModelEntry]


class ToolConfig(BaseModel):
    name: str
    description: str
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: str  # 形如 "tools.weather:get_weather"


class LoopConfig(BaseModel):
    max_iterations: int = 8
    tool_timeout: int = 30


class AgentConfig(BaseModel):
    name: str = "agent"
    # 模型配置现在独立到 models.yaml, 这里只放引用
    # 注意: 不能叫 model_config, 那是 pydantic 内部 class attr, 冲突
    models_file: str = "models.yaml"  # 指向模型配置文件
    default_model: str = ""            # 启动时用的模型, 留空则用 models.yaml 里的 default
    context: ContextConfig
    # 兼容旧版: 如果不想拆 models.yaml, 也可以直接把单个 model 写在这里
    model: Optional[ModelConfig] = None
    tools: list[ToolConfig] = Field(default_factory=list)
    loop: LoopConfig = Field(default_factory=LoopConfig)


def load_config(path: str | Path) -> AgentConfig:
    """从 YAML 文件加载配置, 自动展开 ${ENV}。"""
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    raw = _expand_env(raw)
    return AgentConfig.model_validate(raw)


def load_models_config(path: str | Path) -> ModelsConfig:
    """从 models.yaml 加载模型后端列表。"""
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    raw = _expand_env(raw)
    return ModelsConfig.model_validate(raw)


def find_model_entry(cfg: ModelsConfig, name: Optional[str] = None) -> ModelEntry:
    """按名字找模型配置; name=None 用 default。找不到抛 KeyError 列出可用名。"""
    target = name or cfg.default
    for m in cfg.models:
        if m.name == target:
            return m
    available = [m.name for m in cfg.models]
    raise KeyError(f"模型 {target!r} 不在配置里, 可用: {available}")


def entry_to_model_config(entry: ModelEntry) -> ModelConfig:
    """把 ModelEntry 转成 ModelConfig (给 ModelClient 用)。"""
    return ModelConfig(
        backend=entry.backend,
        name=entry.model,
        base_url=entry.base_url,
        api_key=entry.api_key,
        temperature=entry.temperature,
        max_tokens=entry.max_tokens,
        timeout=entry.timeout,
        stream=entry.stream,
    )
