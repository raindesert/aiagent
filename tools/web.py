"""工具: 抓取 URL, HTML 自动转纯文本。"""
from __future__ import annotations

import re

import requests

_DEFAULT_TIMEOUT = 15
_DEFAULT_MAX_CHARS = 50_000

_HEADERS = {
    "User-Agent": "aiagent/0.1 (+https://github.com/raindesert/aiagent)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def web_fetch(url: str, max_chars: int = _DEFAULT_MAX_CHARS, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """抓取 URL 内容, HTML 自动转纯文本, 超过 max_chars 截断。

    Args:
        url: 完整 URL, 必须以 http:// 或 https:// 开头。
        max_chars: 返回内容最大字符数, 默认 50000。
        timeout: 请求超时秒数, 默认 15。
    """
    if not url or not url.strip():
        return "URL 不能为空"
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return "URL 必须以 http:// 或 https:// 开头"
    if timeout <= 0:
        return "timeout 必须为正整数"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout:
        return f"请求超时 ({timeout}s)"
    except requests.exceptions.SSLError as e:
        return f"SSL 错误: {e}"
    except requests.exceptions.ConnectionError as e:
        return f"连接失败: {e}"
    except requests.exceptions.RequestException as e:
        return f"请求失败: {e}"

    if resp.status_code != 200:
        return f"HTTP {resp.status_code} {resp.reason}"

    final_url = resp.url
    content_type = resp.headers.get("Content-Type", "")
    raw = resp.text
    if "html" in content_type.lower() or "<html" in raw[:200].lower():
        text = _html_to_text(raw)
    else:
        text = raw

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    head = f"# {final_url}\n# Content-Type: {content_type or '?'}  HTTP {resp.status_code}\n"
    if truncated:
        head += f"# (已截断到 {max_chars} 字符, 原文约 {len(raw)} 字符)\n"
    return head + "\n" + text


# ---- HTML 简化 ----
_BLOCK_TAG_RE = re.compile(r"</(p|div|li|h[1-6]|tr|br|hr|pre|blockquote)\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.S | re.I)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.S | re.I)
_A_HREF_RE = re.compile(
    r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a\s*>', re.S | re.I)
_HTML_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&hellip;": "…",
    "&mdash;": "—", "&ndash;": "–",
}


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s).strip()


def _html_to_text(html: str) -> str:
    # 去 script/style
    html = _SCRIPT_RE.sub("", html)
    html = _STYLE_RE.sub("", html)
    # <a href="...">text</a>  ->  text (url)
    html = _A_HREF_RE.sub(
        lambda m: f"{_strip_tags(m.group(2))} ({m.group(1)})", html
    )
    # 块级标签换行
    html = _BLOCK_TAG_RE.sub("\n", html)
    # <br> 不带斜杠也处理
    html = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.I)
    # 去剩余标签
    html = _TAG_RE.sub("", html)
    # HTML 实体
    for k, v in _HTML_ENTITIES.items():
        html = html.replace(k, v)
    # 折叠空行
    html = re.sub(r"\n{3,}", "\n\n", html)
    # 折叠每行内连续空白
    html = re.sub(r"[ \t]+", " ", html)
    return html.strip()
