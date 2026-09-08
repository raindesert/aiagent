"""命令行入口: 启动交互式对话。

用法:
    python cli.py                         # 用默认 agent.yaml
    python cli.py --config my.yaml        # 指定配置
    python cli.py --print-config          # 打印解析后的配置后退出
    python cli.py -m "现在几点了?"         # 单轮模式, 输出回答后退出
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
  {name}  (model: {model})
  tools:  {tools}
  输入 /quit 退出, /reset 清空对话, /tools 查看可用工具
=============================================
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="对话 AI Agent CLI")
    parser.add_argument(
        "-c", "--config", default="agent.yaml", help="配置文件路径 (默认 agent.yaml)"
    )
    parser.add_argument(
        "--print-config", action="store_true", help="打印配置后退出"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="打印 DEBUG 日志"
    )
    parser.add_argument(
        "-m", "--message", default=None, help="单轮模式: 直接发一条消息并打印回答"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"找不到配置文件: {cfg_path}", file=sys.stderr)
        return 2
    cfg = load_config(cfg_path)

    if args.print_config:
        print(json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False))
        return 0

    print(
        BANNER.format(
            name=cfg.name, model=cfg.model.name, tools=cfg.tools[0].name if cfg.tools else "(none)"
        )
    )

    try:
        agent = Agent(cfg)
    except ValueError as e:
        print(f"初始化失败: {e}", file=sys.stderr)
        print(
            "提示: 在 agent.yaml 里设置 model.api_key, 或者设置环境变量 "
            "OPENAI_API_KEY 后用 ${OPENAI_API_KEY} 占位。",
            file=sys.stderr,
        )
        return 1

    if args.message is not None:
        # 单轮模式: assistant 内容打到 stdout
        result = agent.chat(args.message)
        if not result.content:
            print("(模型未返回文本)")
        print(f"\n[iter={result.iterations} tools={result.tool_calls_made}]")
        return 0

    # 交互模式: 流式 token 走 stderr, 最终回答走 stdout
    while True:
        try:
            user_input = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0
        if not user_input:
            continue
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

        result = agent.chat(user_input)
        # 走的是流式: 内容在 model 层已打印到 stderr, 这里给个元信息
        print(f"\n[iter={result.iterations} tools={result.tool_calls_made}]")


if __name__ == "__main__":
    sys.exit(main())
