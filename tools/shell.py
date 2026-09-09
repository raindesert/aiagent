"""工具: 在指定目录执行 shell 命令, 返回 stdout / stderr / exit code。

Windows 默认走 cmd.exe (系统 shell), 调用 PowerShell 语法请用 `powershell -NoProfile -Command "..."`。
为保证中文输出正常, Windows 上自动先 `chcp 65001` 切换到 UTF-8 代码页。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_DEFAULT_TIMEOUT = 30  # 秒
_MAX_OUTPUT_CHARS = 50_000  # 单次输出最大字符


def run_shell(command: str, timeout: int = _DEFAULT_TIMEOUT, cwd: str = ".") -> str:
    """执行一条 shell 命令。

    Args:
        command: shell 命令字符串。
        timeout: 超时秒数, 默认 30。
        cwd: 工作目录, 默认当前目录。
    """
    if not command or not command.strip():
        return "命令不能为空"
    if timeout <= 0:
        return "timeout 必须为正整数"

    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        return f"工作目录不存在: {workdir}"

    # Windows 切到 UTF-8 代码页再执行, 避免中文输出乱码
    if sys.platform == "win32":
        full_cmd = f"chcp 65001 >nul 2>&1 && {command}"
    else:
        full_cmd = command

    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"命令执行超时 ({timeout}s)"
    except FileNotFoundError as e:
        return f"shell 不可用: {e}"
    except OSError as e:
        return f"执行出错: {e}"

    out = (result.stdout or "").rstrip()
    err = (result.stderr or "").rstrip()

    parts: list[str] = []
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
