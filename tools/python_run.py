"""工具: 在子进程跑 Python 代码, 返回 stdout / stderr / exit code。

用 sys.executable 作为解释器, 保证依赖一致; 通过 stdin 喂代码, 避开命令行长度限制。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_DEFAULT_TIMEOUT = 30  # 秒
_MAX_OUTPUT_CHARS = 50_000


def python_run(code: str, timeout: int = _DEFAULT_TIMEOUT, cwd: str = ".") -> str:
    """执行一段 Python 代码。

    Args:
        code: 要执行的 Python 代码 (通过 stdin 传入)。
        timeout: 超时秒数, 默认 30。
        cwd: 工作目录, 默认当前目录。
    """
    if not code or not code.strip():
        return "代码不能为空"
    if timeout <= 0:
        return "timeout 必须为正整数"

    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        return f"工作目录不存在: {workdir}"

    try:
        result = subprocess.run(
            [sys.executable, "-"],
            input=code,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"执行超时 ({timeout}s)"
    except FileNotFoundError as e:
        return f"Python 解释器不可用: {e}"
    except OSError as e:
        return f"执行出错: {e}"

    out = (result.stdout or "").rstrip()
    err = (result.stderr or "").rstrip()

    parts: list[str] = []
    parts.append(f"[python] {sys.executable}")
    parts.append(f"[cwd] {workdir}")
    if out:
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... (stdout 截断, 共 {len(out)} 字符)"
        parts.append(f"[stdout]\n{out}")
    if err:
        if len(err) > _MAX_OUTPUT_CHARS:
            err = err[:_MAX_OUTPUT_CHARS] + f"\n... (stderr 截断, 共 {len(err)} 字符)"
        parts.append(f"[stderr]\n{err}")
    parts.append(f"[exit code] {result.returncode}")
    return "\n\n".join(parts)
