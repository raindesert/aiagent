"""文件读写工具: read_file / write_file / edit_file。

行尾与编码策略 (重要, 否则会静默改坏文件):
- 一律按字节读写, 不做换行转换。`write_file` 写什么就是什么 (`\\n` 不会被 Windows
  文本模式悄悄变成 `\\r\\n`)。
- `edit_file` 读字节 → 严格 UTF-8 解码 → 在 `\\n` 形式下匹配 → 写回时恢复原行尾,
  所以 LF 文件保持 LF, CRLF 文件保持 CRLF。
- `edit_file` 拒绝非 UTF-8 文件 (cp936/GBK 等): 之前用 errors="replace" 读进来,
  非 ASCII 字节变成 U+FFFD, 只要 old 是 ASCII 片段就会匹配成功并按 UTF-8 写回,
  整个文件的非 ASCII 内容被永久写坏。
"""
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
        data = p.read_bytes()
    except OSError as e:
        return f"打开失败: {e}"

    note = ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        # 不是 UTF-8: 用替换字符显示, 但明确告知 (这种文件 edit_file 会拒绝)
        text = data.decode("utf-8", errors="replace")
        note = f"  [非 UTF-8, offset {e.start} 处解码失败, 已用替换字符显示; edit_file 不支持此类文件]"

    lines = text.splitlines()

    start_line = max(0, int(start_line))
    max_lines = min(int(max_lines) if max_lines > 0 else _MAX_READ_LINES, _MAX_READ_LINES)
    if start_line >= len(lines):
        return f"start_line {start_line} 超出文件总行数 {len(lines)}"
    end = min(start_line + max_lines, len(lines))
    selected = lines[start_line:end]

    width = max(4, len(str(end)))
    body = "\n".join(f"{i:>{width}} | {line.rstrip()}" for i, line in enumerate(selected, start=start_line + 1))
    header = f"# {p}  (lines {start_line+1}-{end} / {len(lines)}, {size} bytes){note}"
    return f"{header}\n{body}"


def write_file(
    path: str,
    content: str,
    encoding: str = "utf-8",
) -> str:
    """写入内容到文件, 自动创建父目录, 默认覆盖已有文件。

    按字节写入, 内容里的 `\\n` 不会被改成 `\\r\\n` (行尾由调用方决定)。

    Args:
        path: 文件路径。
        content: 要写入的内容。
        encoding: 文件编码, 默认 utf-8。
    """
    p = Path(path).expanduser()
    try:
        data = content.encode(encoding)
    except (LookupError, UnicodeEncodeError) as e:
        return f"编码失败 (encoding={encoding}): {e}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    except OSError as e:
        return f"写入失败: {e}"
    return f"已写入 {p} ({len(content)} 字符, {len(data)} bytes, encoding={encoding})"


def edit_file(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> str:
    """精确替换文件中的字符串片段, 保持原编码 (只支持 UTF-8) 与原行尾。

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
        data = p.read_bytes()
    except OSError as e:
        return f"读取失败: {e}"

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return (
            f"拒绝修改: {p} 不是 UTF-8 编码 (offset {e.start} 处解码失败, {e.reason})。\n"
            f"edit_file 会把整个文件按 UTF-8 写回, 对非 UTF-8 文件做替换会写坏其中的非 ASCII 内容。\n"
            f"如果确实要改, 请用 write_file(path, 完整新内容, encoding=...) 重写整个文件。"
        )

    newline = _newline_style(data)
    # 统一成 \n 匹配 (这样模型给的 old/new 用 \n 也能匹配 CRLF 文件), 写回时再恢复
    text_lf = _to_lf(text)
    old_lf = _to_lf(old)
    new_lf = _to_lf(new)

    count = text_lf.count(old_lf)
    if count == 0:
        return f"未在 {p} 中找到要替换的文本 (old 与文件内容不匹配)"
    if count > 1 and not replace_all:
        return f"找到 {count} 处匹配, 请给出更精确的 old 内容, 或设置 replace_all=true"

    new_text_lf = text_lf.replace(old_lf, new_lf) if replace_all else text_lf.replace(old_lf, new_lf, 1)
    # 计算第一次替换的行号作为反馈
    first_idx = text_lf.find(old_lf)
    line_no = text_lf.count("\n", 0, first_idx) + 1

    out = new_text_lf if newline == "\n" else new_text_lf.replace("\n", newline)
    try:
        p.write_bytes(out.encode("utf-8"))
    except OSError as e:
        return f"写入失败: {e}"

    extra = f" (替换了 {count} 处)" if replace_all and count > 1 else ""
    shown_nl = "CRLF" if newline == "\r\n" else "LF"
    return f"已修改 {p} (line {line_no}{extra}, {len(old)} -> {len(new)} 字符, 行尾保持 {shown_nl})"


# ---- 内部工具 ----
def _newline_style(data: bytes) -> str:
    """文件主要用哪种行尾 (按出现次数占多数的那种)。"""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _to_lf(text: str) -> str:
    """CRLF → LF, 便于跨行尾匹配。"""
    return text.replace("\r\n", "\n") if "\r\n" in text else text