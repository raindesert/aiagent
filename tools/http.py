"""工具: 通用 HTTP 客户端, 支持 GET/POST/PUT/DELETE/PATCH/HEAD。"""
from __future__ import annotations

import json

import requests

_DEFAULT_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 1_000_000  # 1 MB
_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"}
_SENSITIVE_HEADERS = {"set-cookie", "authorization", "proxy-authenticate", "www-authenticate"}


def http_request(
    method: str,
    url: str,
    headers: str = "",
    body: str = "",
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """发送 HTTP 请求并返回响应摘要 + body。

    Args:
        method: HTTP 方法, 大写, 仅支持 GET/POST/PUT/DELETE/PATCH/HEAD。
        url: 完整 URL。
        headers: 可选, JSON 字符串, 例如 {"Authorization":"Bearer xxx"}。
        body: 请求 body 字符串。
        timeout: 超时秒数, 默认 30。
    """
    if not method or not method.strip():
        return "method 不能为空"
    method = method.strip().upper()
    if method not in _ALLOWED_METHODS:
        return f"不支持的 method: {method} (允许: {sorted(_ALLOWED_METHODS)})"
    if not url or not url.strip():
        return "url 不能为空"
    if not (url.startswith("http://") or url.startswith("https://")):
        return "url 必须以 http:// 或 https:// 开头"
    if timeout <= 0:
        return "timeout 必须为正整数"

    # 解析 headers JSON
    hdr_dict: dict[str, str] = {}
    if headers and headers.strip():
        try:
            hdr_dict = json.loads(headers)
        except json.JSONDecodeError as e:
            return f"headers JSON 解析失败: {e}"
        if not isinstance(hdr_dict, dict):
            return "headers 必须是 JSON object"

    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=hdr_dict,
            data=body or None,
            timeout=timeout,
            stream=True,
        )
    except requests.exceptions.Timeout:
        return f"请求超时 ({timeout}s)"
    except requests.exceptions.SSLError as e:
        return f"SSL 错误: {e}"
    except requests.exceptions.ConnectionError as e:
        return f"连接失败: {e}"
    except requests.exceptions.RequestException as e:
        return f"请求失败: {e}"

    # 读取受控大小的 body
    try:
        raw = resp.content[:_MAX_RESPONSE_BYTES]
        truncated = len(resp.content) > _MAX_RESPONSE_BYTES
    except Exception as e:
        return f"读取响应失败: {e}"

    text = raw.decode("utf-8", errors="replace")

    # 构造摘要
    lines: list[str] = []
    lines.append(f"HTTP {resp.status_code} {resp.reason}")
    lines.append(f"# Final URL: {resp.url}")
    lines.append(f"# Content-Type: {resp.headers.get('Content-Type', '?')}")
    lines.append(f"# Body: {len(resp.content)} bytes" + (" (已截断)" if truncated else ""))
    lines.append("# Headers:")
    for k, v in resp.headers.items():
        if k.lower() in _SENSITIVE_HEADERS:
            lines.append(f"#   {k}: <omitted>")
        else:
            lines.append(f"#   {k}: {v}")
    lines.append("")
    lines.append(text)
    return "\n".join(lines)
