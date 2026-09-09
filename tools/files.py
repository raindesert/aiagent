"""文件读写工具: read_file / write_file / edit_file。"""
from __future__ import annotations

from pathlib import Path

_MAX_READ_BYTES = 2_000_000  # 2 MB
_MAX_READ_LINES = 5000


def read_file(
    path: str,
    start_line: int = 0,
    max_lines: int = 500,
) -> str:
    """读取文件内容, 返回带行号的文本 (类似 cat -n)。

    Args:
        path: 文件路径。
        start_line: 起始行号 (0-indexed), 默认 0。
        max_lines: 最多读取多少行, 默认 500。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"文件不存在: {p}"
    try:
        size = p.stat().st_size
    except OSError as e:
        return f"无法 stat: {e}"
    if size > _MAX_READ_BYTES:
        return f"文件过大 ({size} bytes > {_MAX_READ_BYTES}), 请缩小范围"

    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"打开失败: {e}"

    start_line = max(0, int(start_line))
    max_lines = min(int(max_lines) if max_lines > 0 else _MAX_READ_LINES, _MAX_READ_LINES)
    if start_line >= len(lines):
        return f"start_line {start_line} 超出文件总行数 {len(lines)}"
    end = min(start_line + max_lines, len(lines))
    selected = lines[start_line:end]

    width = max(4, len(str(end)))
    body = "\n".join(f"{i:>{width}} | {line.rstrip()}" for i, line in enumerate(selected, start=start_line + 1))
    header = f"# {p}  (lines {start_line+1}-{end} / {len(lines)}, {size} bytes)"
    return f"{header}\n{body}"


def write_file(
    path: str,
    content: str,
    encoding: str = "utf-8",
) -> str:
    """写入内容到文件, 自动创建父目录, 默认覆盖已有文件。

    Args:
        path: 文件路径。
        content: 要写入的内容。
        encoding: 文件编码, 默认 utf-8。
    """
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding=encoding) as f:
            f.write(content)
    except OSError as e:
        return f"写入失败: {e}"
    return f"已写入 {p} ({len(content)} 字符, encoding={encoding})"


def edit_file(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> str:
    """精确替换文件中的字符串片段。

    Args:
        path: 文件路径。
        old: 要替换的原文本 (必须唯一匹配, 除非 replace_all=true)。
        new: 替换后的文本。
        replace_all: 是否替换全部匹配, 默认 false。
    """
    if not old:
        return "old 不能为空"
    p = Path(path).expanduser()
    if not p.is_file():
        return f"文件不存在: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"读取失败: {e}"

    count = text.count(old)
    if count == 0:
        return f"未在 {p} 中找到要替换的文本 (old 与文件内容不匹配)"
    if count > 1 and not replace_all:
        return f"找到 {count} 处匹配, 请给出更精确的 old 内容, 或设置 replace_all=true"

    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    # 计算第一次替换的行号作为反馈
    first_idx = text.find(old)
    line_no = text.count("\n", 0, first_idx) + 1

    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return f"写入失败: {e}"

    extra = f" (替换了 {count} 处)" if replace_all and count > 1 else ""
    return f"已修改 {p} (line {line_no}{extra}, {len(old)} -> {len(new)} 字符)"
