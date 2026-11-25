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

# (语料, 函数, 参数, **每次计时里的调用次数**)
#
# **必须真跑一批** ✅ —— 旧口径只计时**一次调用** ✅ 再乘以系数 ✅：
# 单次调用的窗口在 1 µs 量级 ✅ -> 噪声（调度/缓存/TSC）压过信号 ✅
# （实测同一用例在 0.20~0.40 ms 之间跳 ✅）。现在在计时区间内跑 `iters` 次 ✅。
CASES = [
    ("corpus_basic", "add", (2, 3), 300000),
    ("corpus_basic", "control", (12,), 20000),
    ("corpus_basic", "strings", ("bob", 3), 50000),
    ("corpus_basic", "containers", ([1, 2, 3, 4, 5],), 10000),
    ("corpus_basic", "mixed", (1, 2, 3, 4), 30000),
    # **常量池** ✅：循环里的字面量 ✅（字符串/bytes 每次新建会明显变慢 ✅）
    ("corpus_consts", "loop_str", (2000,), 100),
    ("corpus_consts", "loop_num", (2000,), 100),
]


def _load_source(name: str):
    """按源码路径加载 ✅（走 importlib ✅ —— 不经我们的编译器 ✅）。"""
    spec = importlib.util.spec_from_file_location("bench_src_" + name, CORPUS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _time(fn, args, iters: int, repeat: int) -> float:
    """在计时区间内跑 `iters` 次 ✅ —— 取**最快**一轮 ✅（减少噪声 ✅）。

    **不要只计时一次调用** ✅：单次窗口太小 ✅，噪声能盖住真实差异 ✅（实测 ✅）。
    """
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        for _ in range(iters):
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
    print("%-28s %12s %12s %9s" % ("case", "source(us)", "aot(us)", "speedup"))
    print("-" * 66)
    total_src = 0.0
    total_aot = 0.0
    for corpus, func, call, iters in CASES:
        src_fn = getattr(sources[corpus], func)
        aot_fn = getattr(aots[corpus], func)
        # **每次调用的微秒数** ✅
        t_src = _time(src_fn, call, iters, args.repeat) / iters * 1e6
        t_aot = _time(aot_fn, call, iters, args.repeat) / iters * 1e6
        total_src += t_src
        total_aot += t_aot
        speed = (t_src / t_aot) if t_aot > 0 else 0.0
        print("%-28s %12.3f %12.3f %8.2fx" % (corpus + "." + func, t_src, t_aot, speed))
    print("-" * 66)
    overall = (total_src / total_aot) if total_aot > 0 else 0.0
    print("%-28s %12.3f %12.3f %8.2fx" % ("TOTAL(按耗时加权)", total_src, total_aot, overall))
    # **几何平均** ✅ —— 每个用例等权 ✅（TOTAL 会被长用例主导 ✅）
    speeds = []
    for corpus, func, call, iters in CASES:
        src_fn = getattr(sources[corpus], func)
        aot_fn = getattr(aots[corpus], func)
        t_src = _time(src_fn, call, iters, args.repeat)
        t_aot = _time(aot_fn, call, iters, args.repeat)
        if t_aot > 0:
            speeds.append(t_src / t_aot)
    geo = 1.0
    for s in speeds:
        geo *= max(s, 1e-9)
    geo = geo ** (1.0 / len(speeds)) if speeds else 0.0
    core = speeds[:5]
    geo_core = 1.0
    for s in core:
        geo_core *= max(s, 1e-9)
    geo_core = geo_core ** (1.0 / len(core)) if core else 0.0
    print("%-28s %12s %12s %8.2fx" % ("GEO-MEAN(全部)", "-", "-", geo))
    print("%-28s %12s %12s %8.2fx" % ("GEO-MEAN(corpus_basic)", "-", "-", geo_core))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())