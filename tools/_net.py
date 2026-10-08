"""HTTP 响应读取小工具 (http_request / web_fetch 共用)。"""
from __future__ import annotations

import re

import requests

_CHUNK = 64 * 1024
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w\-.:]+)", re.I)


def read_capped(resp: requests.Response, limit: int) -> tuple[bytes, bool]:
    """流式读取响应体, 最多留 limit 字节, 返回 (数据, 是否被截断)。

    不能直接读 `resp.content`: 那是先把整个 body 拉进内存再切片, 声明的上限保护不了内存
    (一个 GB 级 body 就能把进程打满)。
    """
    chunks: list[bytes] = []
    got = 0
    for chunk in resp.iter_content(chunk_size=_CHUNK):
        if not chunk:
            continue
        chunks.append(chunk)
        got += len(chunk)
        if got > limit:
            break
    data = b"".join(chunks)
    return data[:limit], len(data) > limit


def charset_from_content_type(content_type: str | None) -> str | None:
    """只取 Content-Type 里**显式声明**的 charset。

    不能用 `requests` 的 `resp.encoding`: 它对没有 charset 的 text/* 会补一个
    'ISO-8859-1' 默认值, 拿它解码会把 UTF-8 的中文页面变成乱码。
    """
    if not content_type:
        return None
    m = _CHARSET_RE.search(content_type)
    return m.group(1) if m else None


def decode_body(data: bytes, charset: str | None = None, *, truncated: bool = False) -> str:
    """按 声明 charset → utf-8 → gb18030 顺序解码, 全部失败则 utf-8 + 替换字符。

    `truncated=True` 表示数据是被字节上限截断的 (末尾可能切在多字节字符中间),
    此时不再尝试 gb18030, 免得把 UTF-8 的半截字符当成 GBK 解出乱码。
    """
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    candidates.append("utf-8")
    if not truncated:
        candidates.append("gb18030")  # 覆盖 GBK/GB2312 等中文站点
    for enc in candidates:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")