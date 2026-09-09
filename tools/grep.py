"""工具: 在目录里按正则搜索文件内容。"""
from __future__ import annotations

import re
from pathlib import Path

_DEFAULT_MAX_RESULTS = 100
_MAX_FILE_BYTES = 2_000_000  # 单文件 2MB 限制
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".idea", ".vscode", "dist", "build", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "site-packages",
}
_BINARY_EXTS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".bz2", ".7z", ".rar", ".mp3", ".mp4", ".mov", ".avi",
    ".mkv", ".wav", ".flac", ".woff", ".woff2", ".ttf", ".eot",
}


def grep_search(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    case_insensitive: bool = False,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> str:
    """在目录中按正则搜索文本, 输出 file:line: snippet 格式。

    Args:
        pattern: 正则表达式 (Python re 语法)。
        path: 搜索根目录, 默认当前目录。
        glob: 文件 glob 过滤, 默认 "*" (所有文件)。
        case_insensitive: 是否忽略大小写, 默认 false。
        max_results: 最多返回多少条匹配, 默认 100。
    """
    if not pattern:
        return "pattern 不能为空"

    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"正则表达式无效: {e}"

    root = Path(path).expanduser()
    if not root.is_dir():
        return f"目录不存在: {root}"

    matches: list[tuple[str, int, str]] = []
    files_scanned = 0
    truncated = False

    try:
        candidates = list(root.rglob(glob))
    except (NotImplementedError, ValueError) as e:
        return f"glob 模式无效: {e}"

    for f in candidates:
        if not f.is_file():
            continue
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        if f.suffix.lower() in _BINARY_EXTS:
            continue
        try:
            size = f.stat().st_size
            if size > _MAX_FILE_BYTES or size == 0:
                continue
        except OSError:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        files_scanned += 1

        for line_no, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                rel = f.relative_to(root).as_posix()
                snippet = line.rstrip()
                if len(snippet) > 240:
                    snippet = snippet[:240] + "..."
                matches.append((rel, line_no, snippet))
                if len(matches) >= max_results:
                    truncated = True
                    break
        if truncated:
            break

    if not matches:
        return f"在 {root} 下未匹配到 {pattern!r} (扫描 {files_scanned} 个文件)"

    file_width = max(len(m[0]) for m in matches)
    file_width = min(max(file_width, 4), 60)  # 限制最大宽度
    out = [
        f"共 {len(matches)} 条匹配, 扫描 {files_scanned} 个文件 (根目录: {root})"
        + (f", 已截断到 {max_results} 条" if truncated else ""),
    ]
    for rel, line_no, snippet in matches:
        out.append(f"{rel:<{file_width}} :{line_no}: {snippet}")
    return "\n".join(out)
