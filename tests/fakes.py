"""测试替身: 脚本化的假模型客户端 + 假 Agent, 让主循环和 CLI 可以离线测。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.config import ModelConfig
from agent.model import ModelResponse, ToolCall


class FakeModel:
    """按脚本回答: "tool"=调一次 get_current_time, "badtool"=调不存在的工具, 其他=出文本。"""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.seen_roles: list[list[str]] = []
        self.cfg = ModelConfig(name="fake", api_key="k", base_url="http://x/v1", stream=False)

    def chat(self, messages, tools=None):
        self.seen_roles.append([m["role"] for m in messages])
        step = self.scripted.pop(0)
        if step == "tool":
            return ModelResponse(
                content="先查一下",
                tool_calls=[ToolCall(id="c1", name="get_current_time", arguments="{}")],
            )
        if step == "badtool":
            return ModelResponse(tool_calls=[ToolCall(id="cb", name="i_do_not_exist", arguments="{}")])
        return ModelResponse(content=step)

    def chat_streaming(self, messages, tools=None, on_token=None):
        resp = self.chat(messages, tools)
        if on_token and resp.content:
            for piece in resp.content:
                on_token(piece)
        return resp


def attach(agent, scripted):
    """把假模型挂到真 Agent 上, 返回该假模型。"""
    fake = FakeModel(scripted)
    agent.model = fake
    return fake


class FakeOpenAI:
    """本地假 OpenAI 服务: 先流式吐一个 tool_call, 再流式吐文本。

    给 UI / 协议层用例当真后端用, 不依赖任何外部服务。
    """

    TOOL_TEXT = "服务器时间 "
    FINAL_TEXT = "10:00:00"

    def __init__(self):
        outer = self
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            # HTTP/1.0: 每次响应完关连接, SSE body 就不用算 Content-Length / chunked
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):  # 别把访问日志混进 pytest 输出
                pass

            def do_GET(self):
                self._json({"object": "list", "data": [{"id": "fake", "object": "model"}]})

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append(body)
                self._sse(outer._chunks_for(len(outer.requests)))

            def _json(self, payload):
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _sse(self, chunks):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for c in chunks:
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    # ---- 服务端脚本 ----
    def _chunks_for(self, turn: int) -> list[dict]:
        if turn == 1:
            return [self._chunk({"tool_calls": [{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }]}, finish="tool_calls")]
        return [
            self._chunk({"content": self.TOOL_TEXT}),
            self._chunk({"content": self.FINAL_TEXT}),
            self._chunk({}, finish="stop"),
        ]

    @staticmethod
    def _chunk(delta: dict, finish: str | None = None) -> dict:
        return {
            "id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0,
            "model": "fake", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
