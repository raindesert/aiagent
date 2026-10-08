"""命令行入口: 启动交互式对话。

用法:
    python cli.py                         # 用默认 agent.yaml
    python cli.py --config my.yaml        # 指定配置
    python cli.py --print-config          # 打印解析后的配置后退出
    python cli.py -m "现在几点了?"         # 单轮模式, 输出回答后退出

交互命令:
    /quit, /exit, :q          退出
    /reset                     清空当前 session 的对话历史
    /tools                     列出可用工具
    /models                    列出所有配置的模型后端
    /model                     显示当前激活的模型
    /model <name>              切换到指定模型 (历史保留)
    /session                   显示当前 session
    /session list              列出所有持久化的 session
    /session new [name]        开新 session (旧 session 自动保存)
    /session switch <id>       切换到指定 session
    /session save              手动保存当前 session
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from agent import Agent, load_config

BANNER = """\
=============================================
  {name}
  model:   {model}  ({base_url})
  session: {session}
  tools:   {tool_count} 个
  命令: /quit /reset /tools /models /model [name] /session
=============================================
"""


def _print_banner(cfg, agent) -> None:
    cur = next((i for i in agent.model_info() if i["current"]), None)
    base_url = cur["base_url"] if cur else "?"
    history_count = len(agent.memory.all_messages())
    session_str = (
        f"{agent.session_id} ({history_count} 条历史)" if agent.session_id else "(无持久化)"
    )
    print(
        BANNER.format(
            name=cfg.name,
            model=agent.current_model_name,
            base_url=base_url or "?",
            session=session_str,
            tool_count=len(cfg.tools),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="对话 AI Agent CLI")
    parser.add_argument(
        "-c", "--config", default="agent.yaml", help="配置文件路径 (默认 agent.yaml)"
    )
    parser.add_argument(
        "--print-config", action="store_true", help="打印配置后退出"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="显示 INFO 级别日志 (默认隐藏)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="显示 DEBUG 级别日志 (比 -v 更详细, 含 tool call 详情)"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="只显示 ERROR 及以上 (静默模式)"
    )
    parser.add_argument(
        "-m", "--message", default=None, help="单轮模式: 直接发一条消息并打印回答"
    )
    args = parser.parse_args()

    # 日志级别
    if args.quiet:
        level = logging.ERROR
    elif args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(level=level, format="[%(levelname)s] %(name)s: %(message)s")
    # banner + 每轮 [iter=N tools=[...]] 统计行都归 -v / --debug 管
    show_stats = level <= logging.INFO

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"找不到配置文件: {cfg_path}", file=sys.stderr)
        return 2
    cfg = load_config(cfg_path)

    if args.print_config:
        print(json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False))
        return 0

    try:
        from agent import MemoryStore
        store = MemoryStore("~/.aiagent/memory.db")
        agent = Agent(cfg, store=store, session_id="default")
    except (ValueError, OSError) as e:
        print(f"初始化失败: {e}", file=sys.stderr)
        print(
            "提示: 在 models.yaml 里设置对应模型的 api_key, "
            "或设置环境变量 (OPENAI_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY) 后用 ${VAR} 占位。",
            file=sys.stderr,
        )
        return 1

    if show_stats:
        _print_banner(cfg, agent)

    if args.message is not None:
        result = agent.chat(args.message)
        if result.content:
            print(result.content)
        else:
            print("(模型未返回文本)")
        if show_stats:
            print(f"\n[iter={result.iterations} tools={result.tool_calls_made}]")
        return 0

    # 交互模式
    while True:
        try:
            user_input = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0
        if not user_input:
            continue

        # ---- 斜杠命令 ----
        if user_input in ("/quit", "/exit", ":q"):
            print("bye.")
            return 0
        if user_input == "/reset":
            agent.reset()
            print("(对话已清空)")
            continue
        if user_input == "/tools":
            for n in agent.tools.names():
                print(" -", n)
            continue
        if user_input == "/models":
            for info in agent.model_info():
                marker = " *" if info["current"] else "  "
                print(f"{marker} {info['name']:<14} {info['model']:<22} {info['base_url']}")
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                # 显示当前
                cur = next((m for m in agent.model_info() if m["current"]), None)
                if cur:
                    print(f"当前模型: {cur['name']} ({cur['model']}, {cur['base_url']})")
                continue
            name = parts[1].strip()
            try:
                print(agent.switch_model(name))
            except (KeyError, ValueError) as e:
                print(f"切换失败: {e}")
                print("用 /models 列出可用模型")
            continue
        if user_input == "/session" or user_input.startswith("/session "):
            parts = user_input.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            sub = arg.split(maxsplit=1)
            verb = sub[0] if sub else ""
            rest = sub[1] if len(sub) > 1 else ""
            if verb in ("", "show"):
                print(f"当前 session: {agent.session_id} ({len(agent.memory.all_messages())} 条历史)")
                continue
            if verb == "list":
                sessions = agent.list_sessions()
                if not sessions:
                    print("(没有持久化的 session)")
                else:
                    for s in sessions:
                        marker = " *" if s["session_id"] == agent.session_id else "  "
                        print(f"{marker} {s['session_id']:<14} {s['msg_count']:>3} 条  {s['updated_at']}  {s['title']}")
                continue
            if verb == "new":
                try:
                    print(agent.new_session(rest.strip() or None))
                except Exception as e:
                    print(f"新建 session 失败: {e}")
                continue
            if verb == "switch":
                if not rest:
                    print("用法: /session switch <id>")
                    continue
                try:
                    print(agent.switch_session(rest.strip()))
                except Exception as e:
                    print(f"切换失败: {e}")
                continue
            if verb == "save":
                try:
                    agent.save(title=rest.strip() or None)
                    print(f"已保存 session '{agent.session_id}'")
                except Exception as e:
                    print(f"保存失败: {e}")
                continue
            print(f"未知子命令: {verb!r} (支持: list / new / switch / save)")
            continue

        # ---- 正常对话 ----
        # 回答统一写 stdout (可被管道/重定向抓取); 模型开了 stream 就逐 token 即时输出。
        # 注意: 模型配 stream=false 时 on_token 根本不会被调用, 必须自己把 result.content
        # 打出来, 否则交互模式下什么都看不到 (之前只有 -m 模式打印了回答)。
        streaming = bool(getattr(agent.model, "cfg", None) and agent.model.cfg.stream)

        def _on_token(t: str) -> None:
            print(t, end="", flush=True)

        result = agent.chat(user_input, on_token=_on_token if streaming else None)
        if streaming:
            print()  # 补换行: 流式 token 不带结尾换行
        elif result.content:
            print(result.content)
        if show_stats:
            print(f"\n[iter={result.iterations} tools={result.tool_calls_made}]")


if __name__ == "__main__":
    sys.exit(main())
