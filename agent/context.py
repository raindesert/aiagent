"""上下文管理: system prompt 模板 + 运行时变量注入。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .config import ContextConfig
from .memory import Memory

# 只把 {标识符} 当占位符。
# 不能用 str.format: 提示词里常写 JSON / 代码示例 ({"ok": true}、{}、{0}),
# 它会把 {"ok": true} 解析成字段名 "ok" + 格式串, 抛 KeyError; 而兜底的
# template.replace("{key}", ...) 又匹配不到原文, 结果是整个模板一个变量都没替换
# ({role} 原样发给模型)。{0} / {} 抛的是 IndexError, 连兜底都进不去。
_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_template(template: str, variables: dict[str, Any]) -> str:
    """替换模板里 `{var}` 形式的占位符。

    - 变量存在 → 替换成它的值 (非 str 自动 str())
    - 变量不存在 → 留成 `<missing:var>` 占位, 便于排查拼错的变量名
    - 其它花括号 (JSON 示例 / `{}` / `{0}` / `{var:spec}`) 原样保留, 不当占位符
    """
    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return str(variables[name])
        return f"<missing:{name}>"

    return _VAR_RE.sub(_sub, template)


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
        return render_template(self.cfg.system_prompt.template, merged)

    def build_messages(
        self,
        memory: Memory,
        *,
        runtime_vars: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回发给模型的完整消息列表: [system, ...history]。"""
        sys_prompt = self.build_system_prompt(runtime_vars)
        return [{"role": "system", "content": sys_prompt}, *memory.to_openai_list()]