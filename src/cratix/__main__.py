"""python -m cratix 入口。

注意：必须有 __main__ 守卫 —— multiprocessing 的 spawn 子进程会重新导入本模块，
无守卫时子进程会把整个构建再跑一遍。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())