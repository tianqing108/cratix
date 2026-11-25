"""一键质量门 ✅ —— 测试 ✅ + 构建 ✅ + 严格 AOT ✅ + 基准 ✅。

用法 ✅：
    python scripts/check.py                 # 全跑 ✅
    python scripts/check.py --skip-bench    # 跳过基准（快 ✅）
    python scripts/check.py --record        # 顺带把基准记进历史 ✅

**为什么要有它** ✅：这几件事必须**每次都一起**跑 ✅ ——
测试绿 ✅ 但构建坏了 ✅ / 构建好 ✅ 但性能掉了 ✅ 都是"假绿" ✅。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env():
    """**cratix 没装进环境** ✅ -> 子进程统一走 `src` ✅（否则 `python -m cratix` 找不到模块 ✅）。"""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT / "src") + ((os.pathsep + existing) if existing else "")
    return env


def _run(title: str, command: list, cwd: Path | None = None) -> tuple:
    """跑一步 ✅ —— 打印耗时与结果 ✅（失败不抛 ✅，交给调用方汇总 ✅）。"""
    print("")
    print("=== " + title + " ===")
    start = time.perf_counter()
    proc = subprocess.run(command, cwd=str(cwd or ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=_env())
    elapsed = time.perf_counter() - start
    ok = proc.returncode == 0
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print("  " + line)
    if not ok:
        for line in (proc.stderr or "").strip().splitlines()[-8:]:
            print("  ! " + line)
    print("  -> " + ("PASS" if ok else "FAIL") + "  (%.1f s)" % elapsed)
    return ok, elapsed


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-bench", action="store_true", help="跳过基准 ✅")
    parser.add_argument("--record", action="store_true", help="把基准写入历史 ✅")
    args = parser.parse_args()

    python = sys.executable
    steps: list = []
    steps.append(_run("单元测试", [python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]))
    example = ROOT / "examples" / "hello"
    steps.append(_run("示例工程构建（严格 AOT）",
                      [python, "-m", "cratix", "build", "--config", "cratix.toml",
                       "--backend", "aot", "--strict-aot"], cwd=example))
    exe = example / "build" / "public" / "hello_0.1.0.exe"
    if exe.is_file():
        steps.append(_run("产物冒烟运行", [python, "-m", "cratix", "verify",
                                      "--config", "cratix.toml", "--zero-disk"], cwd=example))
    if not args.skip_bench:
        bench_cmd = [python, "tests/bench/bench_aot.py", "--repeat", "5", "--history", "3"]
        if args.record:
            bench_cmd.append("--record")
        steps.append(_run("性能基准", bench_cmd))

    total = sum(elapsed for _ok, elapsed in steps)
    failed = [title for (ok, _elapsed), title in zip(steps, ["单元测试", "构建", "冒烟", "基准"]) if not ok]
    print("")
    print("=" * 60)
    print("总耗时 %.1f s" % total)
    if failed:
        print("质量门：FAIL ✅ -> " + ", ".join(failed))
        return 1
    print("质量门：PASS ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
