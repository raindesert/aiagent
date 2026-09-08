"""示例 tool: 在目录里按 glob 搜索文件。"""
from __future__ import annotations

import os
from pathlib import Path


def search_files(pattern: str, path: str = ".", limit: int = 20) -> str:
    """在指定目录下按 glob 模式搜索文件, 返回最多 limit 条相对路径。

    Args:
        pattern: glob 模式, 例如 "*.py"、"**/*.md"。
        path: 搜索根目录, 默认当前目录。
        limit: 最多返回多少条, 默认 20。
    """
    try:
        root = Path(path).expanduser().resolve()
    except OSError as e:
        return f"路径解析失败: {e}"
    if not root.exists():
        return f"目录不存在: {root}"
    if not root.is_dir():
        return f"不是目录: {root}"

    try:
        matches = sorted(root.glob(pattern))
    except (NotImplementedError, ValueError) as e:
        return f"glob 模式无效: {e}"

    if not matches:
        return f"在 {root} 下没有匹配 {pattern!r} 的文件"

    lines: list[str] = []
    for p in matches[:limit]:
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            rel = str(p)
        if p.is_dir():
            rel += "/"
        size = p.stat().st_size if p.is_file() else 0
        lines.append(f"{rel}  ({_fmt_size(size)})")
    summary = f"共 {len(matches)} 条, 显示前 {min(limit, len(matches))} 条 (根目录: {root})"
    return summary + "\n" + "\n".join(lines)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
