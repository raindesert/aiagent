"""Tool 注册表: 配置 → schema → 执行。"""
from __future__ import annotations

import importlib
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import dataclass
from typing import Any, Callable

from .config import ToolConfig

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None

    def to_message(self) -> str:
        if self.ok:
            return self.output if isinstance(self.output, str) else json.dumps(
                self.output, ensure_ascii=False, indent=2
            )
        return f"[tool error] {self.error}"


def _import_handler(dotted: str) -> Callable[..., Any]:
    """从 'pkg.mod:func' 字符串导入函数。"""
    if ":" not in dotted:
        raise ValueError(f"handler 格式错误, 应为 'pkg.mod:func', 实际: {dotted!r}")
    mod_path, attr = dotted.split(":", 1)
    mod = importlib.import_module(mod_path)
    fn = getattr(mod, attr, None)
    if fn is None:
        raise ValueError(f"{mod_path} 里找不到 {attr}")
    return fn


class ToolRegistry:
    def __init__(self, tool_cfgs: list[ToolConfig]):
        self._tools: dict[str, ToolConfig] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        for cfg in tool_cfgs:
            if not cfg.enabled:
                continue
            try:
                self._handlers[cfg.name] = _import_handler(cfg.handler)
            except Exception as e:
                log.warning("工具 %s 加载失败 (%s): %s", cfg.name, cfg.handler, e)
                continue
            self._tools[cfg.name] = cfg

    # ---------- metadata ----------
    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        """返回 OpenAI 风格的 tools schema。"""
        out: list[dict[str, Any]] = []
        for cfg in self._tools.values():
            out.append({
                "type": "function",
                "function": {
                    "name": cfg.name,
                    "description": cfg.description,
                    "parameters": cfg.parameters,
                },
            })
        return out

    # ---------- execution ----------
    def execute(self, name: str, arguments: str, timeout: int = 30) -> ToolResult:
        if name not in self._tools:
            return ToolResult(ok=False, error=f"未知工具: {name}")
        try:
            args = json.loads(arguments) if arguments else {}
            if not isinstance(args, dict):
                return ToolResult(ok=False, error="工具参数必须是 JSON object")
        except json.JSONDecodeError as e:
            return ToolResult(ok=False, error=f"参数 JSON 解析失败: {e}")

        # 按 schema 的 required 做客户端校验, 避免 Python TypeError 噪音
        params = self._tools[name].parameters or {}
        required = params.get("required", []) or []
        missing = [k for k in required if k not in args]
        if missing:
            return ToolResult(
                ok=False, error=f"缺少必填参数: {', '.join(missing)}"
            )

        handler = self._handlers[name]
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(handler, **args)
                try:
                    out = future.result(timeout=timeout)
                except FutTimeout:
                    return ToolResult(ok=False, error=f"执行超时 ({timeout}s)")
        except Exception:
            return ToolResult(ok=False, error=traceback.format_exc(limit=3))
        return ToolResult(ok=True, output=out)
