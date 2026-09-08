"""OpenAI 兼容后端的模型客户端。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

from openai import OpenAI

from .config import ModelConfig

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 原始 JSON 字符串


@dataclass
class ModelResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ModelClient:
    """OpenAI 兼容协议的 chat completions 客户端。

    兼容任何提供 /v1/chat/completions 端点的服务:
    - OpenAI
    - DeepSeek / 通义千问 / 智谱 / Moonshot 等
    - 本地 Ollama (http://localhost:11434/v1)
    - llama.cpp server (--api 或 llama-server)
    """

    def __init__(self, cfg: ModelConfig):
        if not cfg.api_key:
            raise ValueError(
                "model.api_key 为空, 请在 agent.yaml 里设置, 或导出环境变量 "
                "OPENAI_API_KEY 后用 ${OPENAI_API_KEY} 占位"
            )
        self.cfg = cfg
        self.client = OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
        )

    # ---------- public ----------
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.cfg.name,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            # 让模型在不确定时不要乱调 tool
            kwargs["tool_choice"] = "auto"

        if self.cfg.stream:
            return self._chat_stream(kwargs)
        return self._chat_blocking(kwargs)

    def chat_stream_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[str]:
        """逐 token 产出 assistant 内容, 不返回 tool_call 信息 (tool_call 场景请用 chat)。"""
        kwargs: dict[str, Any] = {
            "model": self.cfg.name,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = self.client.chat.completions.create(**kwargs)
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta
            except (IndexError, AttributeError):
                continue
            piece = getattr(delta, "content", None)
            if piece:
                yield piece

    # ---------- internals ----------
    def _chat_blocking(self, kwargs: dict[str, Any]) -> ModelResponse:
        resp = self.client.chat.completions.create(**kwargs, stream=False)
        choice = resp.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            args = tc.function.arguments or "{}"
            # arguments 可能是 str (标准) 或 dict (部分兼容实现)
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        usage = {}
        if resp.usage:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                "total_tokens": getattr(resp.usage, "total_tokens", 0),
            }
        return ModelResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            usage=usage,
        )

    def _chat_stream(self, kwargs: dict[str, Any]) -> ModelResponse:
        """流式: 仍然要把 tool_calls 收齐再返回, 内容部分边收边打印。"""
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        stream = self.client.chat.completions.create(**kwargs)

        content_buf: list[str] = []
        tool_buf: dict[int, dict[str, str]] = {}  # index -> {id, name, arguments}
        finish_reason = ""
        usage: dict[str, Any] = {}

        for chunk in stream:
            if not chunk.choices and chunk.usage:
                usage = {
                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                    "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                }
                continue
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            piece = getattr(delta, "content", None)
            if piece:
                content_buf.append(piece)
                # 写到 stderr, 避免干扰 stdout 抓取
                print(piece, end="", flush=True, file=__import__("sys").stderr)
            for tc in (getattr(delta, "tool_calls", None) or []):
                idx = tc.index if tc.index is not None else 0
                slot = tool_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

        if content_buf and not finish_reason:
            # 结束换行
            print(file=__import__("sys").stderr)

        tool_calls: list[ToolCall] = []
        for slot in tool_buf.values():
            if not slot["name"]:
                continue
            tool_calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{len(tool_calls)}",
                    name=slot["name"],
                    arguments=slot["arguments"] or "{}",
                )
            )

        return ModelResponse(
            content="".join(content_buf) if content_buf else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )
