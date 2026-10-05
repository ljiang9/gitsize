"""python -m gitsize 入口。"""
import sys

try:
    from gitsize import main
except ImportError:  # 直接在项目目录里 python __main__.py 运行
    from gitsize import main  # type: ignore

if __name__ == "__main__":
    sys.exit(main())
