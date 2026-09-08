"""示例 tool: 通过 HTTP 发送 RabbitMQ 消息。

调用方: GET http://127.0.0.1:8081/rabbit/send?msg=<message>
"""
from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# 写死的发送端点, 如需切换可在调用前改这里或拆成配置
_RABBIT_URL = "http://127.0.0.1:8081/rabbit/send"
_DEFAULT_TIMEOUT = 10  # 秒


def send_rabbit_message(message: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """通过 RabbitMQ HTTP 网关发送一条消息。

    Args:
        message: 要发送的消息内容, 会作为 msg 查询参数发送 (URL 自动 encode)。
        timeout: HTTP 请求超时秒数, 默认 10。
    """
    if not message:
        return "消息内容不能为空"
    if timeout <= 0:
        return "timeout 必须为正整数"

    url = f"{_RABBIT_URL}?msg={quote(message)}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return f"已发送 (HTTP {resp.status}): {body or 'ok'}"
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"发送失败 HTTP {e.code} {e.reason}: {err_body}"
    except URLError as e:
        return f"无法连接 {_RABBIT_URL}: {e.reason}"
    except TimeoutError:
        return f"请求超时 ({timeout}s)"
