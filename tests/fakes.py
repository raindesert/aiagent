"""测试替身: 脚本化的假模型客户端 + 假 Agent, 让主循环和 CLI 可以离线测。"""
from __future__ import annotations

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
