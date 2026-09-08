"""Pydantic 配置模型 + YAML 加载。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

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
    context: ContextConfig
    model: ModelConfig
    tools: list[ToolConfig] = Field(default_factory=list)
    loop: LoopConfig = Field(default_factory=LoopConfig)


def load_config(path: str | Path) -> AgentConfig:
    """从 YAML 文件加载配置, 自动展开 ${ENV}。"""
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    raw = _expand_env(raw)
    return AgentConfig.model_validate(raw)
