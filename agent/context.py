"""上下文管理: system prompt 模板 + 运行时变量注入。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import ContextConfig
from .memory import Memory


class ContextManager:
    def __init__(self, cfg: ContextConfig):
        self.cfg = cfg

    def build_system_prompt(self, extra: dict[str, str] | None = None) -> str:
        """渲染 system prompt, 注入配置里的静态变量 + 调用时的动态变量。"""
        runtime: dict[str, str] = {
            "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if extra:
            runtime.update(extra)
        merged = {**self.cfg.system_prompt.variables, **runtime}
        try:
            return self.cfg.system_prompt.template.format(**merged)
        except KeyError as e:
            # 模板里引用了不存在的变量, 用占位符兜底
            return self.cfg.system_prompt.template.replace(
                "{" + e.args[0] + "}", f"<missing:{e.args[0]}>"
            )

    def build_messages(
        self,
        memory: Memory,
        *,
        runtime_vars: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回发给模型的完整消息列表: [system, ...history]。"""
        sys_prompt = self.build_system_prompt(runtime_vars)
        return [{"role": "system", "content": sys_prompt}, *memory.to_openai_list()]
