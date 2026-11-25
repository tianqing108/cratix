"""AOT vs 源码版性能基准 ✅。

**为什么要自己测** ✅：AOT 的价值在于"局部变量进 C 局部 ✅ + 属性走槽位 ✅ + 常量折叠 ✅"——
必须用真实负载量化 ✅ ✅（不能靠感觉 ✅ ✅）。

用法：
    python tests/bench/bench_aot.py            # 跑全部用例 ✅
    python tests/bench/bench_aot.py --repeat 20
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler import driver, loader

CORPUS = ROOT / "tests" / "corpus"

# (语料, 函数, 参数, 迭代次数)
# 迭代次数要够大 ✅ —— 否则测量窗口太小 ✅（0.01 ms 级 ✅）噪声压过信号 ✅ ✅。
CASES = [
    ("corpus_basic", "add", (2, 3), 2000000),
    ("corpus_basic", "control", (12,), 200000),
    ("corpus_basic", "strings", ("bob", 3), 200000),
    ("corpus_basic", "containers", ([1, 2, 3, 4, 5],), 100000),
    ("corpus_basic", "mixed", (1, 2, 3, 4), 200000),
    # **常量池** ✅：循环里的字面量 ✅（字符串/bytes 每次新建会明显变慢 ✅）
    ("corpus_consts", "loop_str", (20000,), 20),
    ("corpus_consts", "loop_num", (20000,), 20),
]


def _load_source(name: str):
    """按源码路径加载 ✅（走 importlib ✅ —— 不经我们的编译器 ✅）。"""
    spec = importlib.util.spec_from_file_location("bench_src_" + name, CORPUS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _time(fn, args, repeat: int) -> float:
    """跑 repeat 轮 ✅ —— 取**最快**一轮 ✅（减少噪声 ✅）。

    轮数要多 ✅ —— 机器负载波动很大 ✅（实测同一用例 0.20~0.40 ms ✅）✅。
    """
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn(*args)
        elapsed = time.perf_counter() - start
        if elapsed < best:
            best = elapsed
    return best


def main() -> int:
    """跑基准 ✅ —— 输出表格 ✅。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=9, help="每例重复轮数（取最快 ✅）")
    args = parser.parse_args()
    out_dir = ROOT / "build" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = {}
    aots = {}
    for corpus, func, _call, _n in CASES:
        if corpus in sources:
            continue
        sources[corpus] = _load_source(corpus)
        mc = driver.compile_module(CORPUS / (corpus + ".py"), "bench_" + corpus)
        pyd = driver.build_pyd(mc, out_dir)
        aots[corpus] = loader.load_compiled_module(pyd, mc.init_symbol)
    print("%-28s %12s %12s %9s" % ("case", "source(ms)", "aot(ms)", "speedup"))
    print("-" * 66)
    total_src = 0.0
    total_aot = 0.0
    for corpus, func, call, iters in CASES:
        src_fn = getattr(sources[corpus], func)
        aot_fn = getattr(aots[corpus], func)
        n = max(1, iters // 1000)
        t_src = _time(src_fn, call, args.repeat) * n
        t_aot = _time(aot_fn, call, args.repeat) * n
        total_src += t_src
        total_aot += t_aot
        speed = (t_src / t_aot) if t_aot > 0 else 0.0
        print("%-28s %12.2f %12.2f %8.2fx" % (corpus + "." + func, t_src * 1000, t_aot * 1000, speed))
    print("-" * 66)
    overall = (total_src / total_aot) if total_aot > 0 else 0.0
    print("%-28s %12.2f %12.2f %8.2fx" % ("TOTAL", total_src * 1000, total_aot * 1000, overall))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())