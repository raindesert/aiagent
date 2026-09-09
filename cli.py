"""命令行入口: 启动交互式对话。

用法:
    python cli.py                         # 用默认 agent.yaml
    python cli.py --config my.yaml        # 指定配置
    python cli.py --print-config          # 打印解析后的配置后退出
    python cli.py -m "现在几点了?"         # 单轮模式, 输出回答后退出

交互命令:
    /quit, /exit, :q       退出
    /reset                  清空对话历史
    /tools                  列出可用工具
    /models                 列出所有配置的模型后端
    /model                  显示当前激活的模型
    /model <name>           切换到指定模型 (历史保留)
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
  model: {model}  ({base_url})
  tools: {tool_count} 个
  命令: /quit /reset /tools /models /model [name]
=============================================
"""


def _print_banner(cfg, current_name: str) -> None:
    base_url = ""
    for m in cfg._models_cfg.models if hasattr(cfg, "_models_cfg") else []:
        if m.name == current_name:
            base_url = m.base_url
            break
    print(
        BANNER.format(
            name=cfg.name,
            model=current_name,
            base_url=base_url or "?",
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

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"找不到配置文件: {cfg_path}", file=sys.stderr)
        return 2
    cfg = load_config(cfg_path)

    if args.print_config:
        print(json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False))
        return 0

    try:
        agent = Agent(cfg)
    except ValueError as e:
        print(f"初始化失败: {e}", file=sys.stderr)
        print(
            "提示: 在 models.yaml 里设置对应模型的 api_key, "
            "或设置环境变量 (OPENAI_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY) 后用 ${VAR} 占位。",
            file=sys.stderr,
        )
        return 1

    # 拿一份 models_file 的 models 列表, 给 banner 用
    from agent import load_models_config
    models_cfg = load_models_config(cfg.models_file)
    cfg._models_cfg = models_cfg  # 临时挂一下, banner 用

    _print_banner(cfg, agent.current_model_name)

    if args.message is not None:
        result = agent.chat(args.message)
        if not result.content:
            print("(模型未返回文本)")
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

        # ---- 正常对话 ----
        result = agent.chat(user_input)
        print(f"\n[iter={result.iterations} tools={result.tool_calls_made}]")


if __name__ == "__main__":
    sys.exit(main())
