"""工具: 在指定目录执行 shell 命令, 返回 stdout / stderr / exit code。

Windows 默认走 PowerShell (`powershell -NoProfile -NonInteractive -EncodedCommand`),
用 UTF-16 LE + base64 编码避免引号转义问题; 内部先切 `[Console]::OutputEncoding = UTF8`
解决 PowerShell 5.1 中文输出乱码。
Linux / macOS 走 /bin/sh -c。

安全: 内置危险命令白名单拦截 (递归删除 / 格式化磁盘 / 关机 / fork bomb 等)。
检测到危险操作时拒绝执行, 必须显式设置 confirm=true 才能跑, 实际部署中由模型把这个信息转给用户确认。
"""
from __future__ import annotations

import base64
import re
import subprocess
import sys
from pathlib import Path

_DEFAULT_TIMEOUT = 30  # 秒
_MAX_OUTPUT_CHARS = 50_000  # 单次输出最大字符

# PowerShell 前缀: 关闭 progress + 把 stdout/stderr 编码切到 UTF-8
# (Information stream 噪音由 Python 端正则清掉, 因为 Write-Host 走 host stream 没法 redirect)
_PS_UTF8_PREFIX = (
    "$ProgressPreference = 'SilentlyContinue'; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
)

# PowerShell 在 stderr 写的 CLIXML 噪音 (Write-Host 等会触发), 整体去掉
_CLIXML_RE = re.compile(r"#<\s*CLIXML\s*<Objs.*?</Objs>\s*", re.DOTALL)


def _clean_stderr(s: str) -> str:
    return _CLIXML_RE.sub("", s).rstrip()


# ---- 危险命令检测 ----
# 匹配模式 → 中文描述. 大小写不敏感.
# 只列强制不可逆的操作, 普通 rm/del 单文件不在此列.
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # 递归删除 (跨平台)
    (r'\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*[fF]|-[a-zA-Z]*[fF][a-zA-Z]*[rR])\b', 'rm -rf/-fr (递归删除)'),
    (r'\brm\s+--recursive\b', 'rm --recursive'),
    (r'\brmdir\s+/[sS]\s+/[qQ]\b', 'rmdir /s /q (cmd 递归删除)'),
    (r'\bdel\s+/[sS]\s+/[qQ]\b', 'del /s /q (cmd 递归删除)'),
    (r'\bRemove-Item\b[^|]*?-[rR]ecurse', 'Remove-Item -Recurse (PowerShell 递归删除)'),
    # 格式化 / 写裸设备
    (r'\bformat\s+[a-zA-Z]:', 'format <盘符> (格式化磁盘)'),
    (r'\bFormat-Volume\b', 'Format-Volume (PowerShell)'),
    (r'\bClear-Disk\b', 'Clear-Disk (PowerShell 清空磁盘)'),
    (r'\bdiskpart\b', 'diskpart (磁盘分区管理)'),
    (r'\bInitialize-Disk\b', 'Initialize-Disk (初始化磁盘)'),
    (r'\bmkfs(?:\.[a-z0-9]+)?\b', 'mkfs (创建文件系统)'),
    (r'\bfdisk\b', 'fdisk (磁盘分区)'),
    (r'\bparted\b', 'parted (磁盘分区)'),
    (r'\bdd\s+[^|]*\bof=/dev/(?:sd|hd|nvme|vd|mmcblk|loop)', 'dd 写裸设备'),
    # 启动破坏
    (r'\bbcdedit\b[^|]*\bdelete', 'bcdedit /delete (破坏启动)'),
    # 关机 / 重启
    (r'\b(?:shutdown|reboot|halt|poweroff)\b', '关机/重启命令'),
    (r'\binit\s+[06]\b', 'init 0/6 (关机/重启)'),
    (r'\bStop-Computer\b', 'Stop-Computer (关机)'),
    (r'\bRestart-Computer\b', 'Restart-Computer (重启)'),
    # Fork bomb
    (r':\s*\(\s*\)\s*\{[^}]*\|\s*:\s*&\s*\}\s*;\s*:', 'fork bomb'),
    # 磁盘擦除 / 覆写
    (r'\bcipher\s+/[wW]\b', 'cipher /w (擦除空闲空间)'),
    (r'\bsdelete\b', 'sdelete (磁盘擦除)'),
    (r'\bshred\b', 'shred (覆写文件)'),
    # 远程下载并执行
    (r'\bcurl\b[^|]*\|\s*(?:sh|bash|zsh|python)', 'curl | sh/bash/python (下载并执行)'),
    (r'\bwget\b[^|]*\|\s*(?:sh|bash|zsh|python)', 'wget | sh/bash/python (下载并执行)'),
    (r'\biwr\b[^|]*\|\s*(?:iex|Invoke-Expression)', 'iwr | iex (下载并执行)'),
    # 危险 system 操作
    (r'\bnet\s+user\s+\S+\s+/delete\b', 'net user /delete'),
    (r'\bnet\s+share\s+\S+\s+/delete\b', 'net share /delete'),
    (r'\breg\s+delete\b', 'reg delete (注册表)'),
    (r'\bRemove-Item\b[^|]*-Path\s+["\']?HK', 'Remove-Item HK* (注册表)'),
]


def _check_dangerous(command: str) -> str | None:
    """返回第一个匹配的危险描述; 没匹配返回 None。"""
    for pat, desc in _DANGEROUS_PATTERNS:
        if re.search(pat, command, flags=re.IGNORECASE):
            return desc
    return None


def _build_argv(command: str) -> list[str]:
    """根据平台构造实际的 shell 调用参数 (list, 不经 shell=True 解析)。"""
    if sys.platform == "win32":
        # 用 EncodedCommand + UTF-16 LE + base64, 完全避开引号/特殊字符问题
        full = _PS_UTF8_PREFIX + command
        encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
        return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    # POSIX
    return ["/bin/sh", "-c", command]


def run_shell(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT,
    cwd: str = ".",
    confirm: bool = False,
) -> str:
    """执行一条 shell 命令。

    Args:
        command: shell 命令字符串 (Windows 上按 PowerShell 语法解析)。
        timeout: 超时秒数, 默认 30。
        cwd: 工作目录, 默认当前目录。
        confirm: 是否确认执行检测到的危险操作, 默认 false。
                 检测到危险命令时必须设为 true 才能执行 (模型应先向用户确认)。
    """
    if not command or not command.strip():
        return "命令不能为空"
    if timeout <= 0:
        return "timeout 必须为正整数"

    # ---- 安全检查 ----
    danger = _check_dangerous(command)
    if danger and not confirm:
        return (
            f"⛔ 命令被安全检查拦截: 检测到危险操作 \"{danger}\"\n"
            f"原命令: {command}\n"
            f"\n"
            f"如需执行, 请:\n"
            f"  1. 先向用户说明这个命令会做什么、影响哪些路径\n"
            f"  2. 取得用户明确同意后, 重新调用 run_shell 并设置 confirm=true\n"
            f"\n"
            f"或: 修改命令避开危险模式 (例如 `rm -rf` → `rm -ri` 交互式, "
            f"或改用 Python `shutil.rmtree` 走更可控的路径)"
        )

    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        return f"工作目录不存在: {workdir}"

    argv = _build_argv(command)
    shell_name = "powershell" if sys.platform == "win32" else "/bin/sh"
    prefix = f"⚠️ [已确认执行危险操作: {danger}]\n" if danger else ""

    try:
        result = subprocess.run(
            argv,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"{prefix}命令执行超时 ({timeout}s)"
    except FileNotFoundError as e:
        return f"{prefix}{shell_name} 不可用: {e}"
    except OSError as e:
        return f"{prefix}执行出错: {e}"

    out = (result.stdout or "").rstrip()
    err = _clean_stderr(result.stderr or "")

    parts: list[str] = []
    if prefix:
        parts.append(prefix.rstrip())
    parts.append(f"[shell] {shell_name}")
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
