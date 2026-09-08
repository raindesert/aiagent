"""main.py 是 cli.py 的薄别名, 方便 `python main.py ...` 调用。"""
from cli import main

if __name__ == "__main__":
    raise SystemExit(main())
