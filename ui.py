"""Web UI 入口 (streamlit): 浏览器里和同一个 agent 对话。

启动:
    python -m streamlit run ui.py                 # 默认 agent.yaml
    python -m streamlit run ui.py --server.port 8601
    python -m streamlit run ui.py --server.headless true

配置/模型/session 全部复用 CLI 那一套 (agent.yaml + models.yaml + ~/.aiagent/memory.db),
所以命令行里聊到一半的 session 在网页上能接着聊, 反之亦然。
"""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from agent import Agent, MemoryStore, load_config
from agent.ui_support import history_view, stream_turn

ROOT = Path(__file__).resolve().parent
# agent.yaml 里的 models_file 和工具默认 cwd 都是相对路径, 锁到仓库根目录最省事
os.chdir(ROOT)

DB_PATH = "~/.aiagent/memory.db"

st.set_page_config(page_title="AI Agent", layout="wide")


@st.cache_resource
def get_store(path: str) -> MemoryStore:
    return MemoryStore(path)


def build_agent(cfg_path: str, session_id: str) -> Agent | None:
    try:
        cfg = load_config(cfg_path)
    except FileNotFoundError:
        st.error(f"找不到配置文件: {ROOT / cfg_path}")
        return None
    except Exception as e:
        st.error(f"配置解析失败 ({type(e).__name__}): {e}")
        return None
    try:
        return Agent(cfg, session_id=session_id, store=get_store(DB_PATH))
    except ValueError as e:
        st.error(f"初始化失败: {e}")
        st.caption(
            "提示: 在 models.yaml 里给对应模型填 api_key, 或设好环境变量后用 ${VAR} 占位。"
        )
        return None


def render_tool(item: dict) -> None:
    result = item["result"] if item["result"] is not None else "(无结果)"
    args = item["arguments"]
    # 参数可能是整段 python 代码, 标题里只留个头
    label = f"工具 {item['name']} {args[:60] + ('…' if len(args) > 60 else '')}"
    with st.expander(label):
        if args:
            st.code(args, language="text")
        st.code(result, language="text")


ss = st.session_state
cfg_input = ss.setdefault("cfg_input", "agent.yaml")
if "agent" not in ss or ss.get("agent_cfg") != cfg_input:
    agent = build_agent(cfg_input, ss.get("session_id", "default"))
    if agent is None:
        st.stop()
    ss.agent = agent
    ss.agent_cfg = cfg_input
agent: Agent = ss.agent

# ---------------- 侧边栏 ----------------
with st.sidebar:
    st.subheader("配置")
    new_cfg = st.text_input("agent 配置", value=cfg_input, key="cfg_widget")
    if new_cfg != cfg_input:
        ss.cfg_input = new_cfg
        ss.session_id = agent.session_id
        st.rerun()

    st.subheader("模型")
    infos = agent.model_info()
    names = [i["name"] for i in infos]
    picked = st.selectbox(
        "后端", names, index=names.index(agent.current_model_name), key="model_widget"
    )
    if picked != agent.current_model_name:
        st.caption(agent.switch_model(picked))
    cur = next(i for i in agent.model_info() if i["current"])
    st.caption(f"`{cur['model']}` @ {cur['base_url']}")

    st.subheader("会话")
    sessions = agent.list_sessions()
    ids = [s["session_id"] for s in sessions]
    if agent.session_id not in ids:
        ids.insert(0, agent.session_id)
    labels = {
        s["session_id"]: f"{s['session_id']} · {s['msg_count']} 条 · {s['title'] or ''}"
        for s in sessions
    }
    # 点过"新建"以后下拉框里还留着旧 id, 不改回跟随值会立刻把新 session 拽回去
    if ss.pop("session_changed", False):
        ss.session_widget = agent.session_id
    target = st.selectbox(
        "session",
        ids,
        index=ids.index(agent.session_id),
        format_func=lambda i: labels.get(i, i),
        key="session_widget",
    )
    col_new, col_reset = st.columns(2)
    if col_new.button("新建", width="stretch"):
        agent.new_session()
        ss.session_changed = True
        st.rerun()
    if col_reset.button("清空", width="stretch"):
        agent.reset()
        st.rerun()
    if target != agent.session_id:
        agent.switch_session(target)
        st.rerun()

    with st.expander(f"工具 ({len(agent.tools.names())} 个)"):
        for n in agent.tools.names():
            st.write(f"- `{n}`")
    with st.expander("系统提示词"):
        st.code(agent.context.build_system_prompt(), language="markdown")

    show_stats = st.checkbox("显示每轮 iter/工具统计", key="show_stats")

# ---------------- 对话区 ----------------
st.title(agent.cfg.name)
st.caption(
    f"model: `{agent.current_model_name}` · session: `{agent.session_id}` · "
    f"历史 {len(agent.memory.messages())} 条"
)

for item in history_view(agent.memory.messages()):
    if item["kind"] == "chat":
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
    else:
        render_tool(item)

prompt = st.chat_input("发消息, Enter 发送")
if prompt:
    if prompt.startswith("/"):
        st.info("网页端不解析斜杠命令, 清空/新建会话/切模型请用左侧栏。")
        st.stop()
    with st.chat_message("user"):
        st.markdown(prompt)
    before = len(agent.memory.messages())
    # 这一轮的工具调用是中途产生的, 上面的历史渲染还没看到; 先占个位,
    # 等回答流完再回填, 位置仍然在回答气泡上面 (和历史渲染顺序一致)
    tools_slot = st.container()
    holder: dict = {}
    with st.chat_message("assistant"):
        def tokens():
            # stream_turn 先吐 token 再吐 result; write_stream 只吃 token
            for kind, payload in stream_turn(agent, prompt):
                if kind == "result":
                    holder["result"] = payload
                    continue
                yield payload

        try:
            st.write_stream(tokens())
        except Exception as e:
            st.error(f"这轮失败 ({type(e).__name__}): {e}")

    with tools_slot:
        for item in history_view(agent.memory.messages()[before:]):
            if item["kind"] == "tool":
                render_tool(item)

    if show_stats and "result" in holder:
        r = holder["result"]
        st.caption(f"iter={r.iterations} tools={r.tool_calls_made}")
