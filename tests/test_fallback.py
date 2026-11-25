"""AOT 测试 ✅ —— 在**独立子进程**里跑 ✅。

理由 ✅：同一进程里连续加载多个 AOT 模块会破坏解释器状态 ✅
（3.14 的自适应字节码缓存 ✅ —— "Executing a cache" ✅）。
产品路径不受影响 ✅（一个应用只加载自己的模块 ✅）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests" / "aot_worker.py"


def run_worker(op: str) -> dict:
    """在独立子进程里跑一个 AOT 操作 ✅ —— 返回结果 JSON ✅。"""
    out_dir = ROOT / "build" / ("aot_" + op)
    proc = subprocess.run(
        [sys.executable, str(WORKER), op, str(out_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise AssertionError("AOT 子进程失败：" + proc.stderr[-1500:])
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("AOT 子进程输出异常：" + proc.stdout[-800:] + proc.stderr[-800:]) from exc
    if not data.get("ok"):
        raise AssertionError("AOT 子进程报错：" + str(data.get("error")))
    return data

class FunctionFallbackTest(unittest.TestCase):
    def test_01_unsupported_functions_are_embedded(self):
        data = run_worker("fallback")
        self.assertEqual(data["fallback"], ["closure", "gen", "uses_global"])
        self.assertEqual(data["embedded"], ["closure", "gen", "uses_global"])

    def test_02_try_except_is_supported(self):
        data = run_worker("try")
        self.assertIn("safe_div", data["supported"])
        self.assertEqual(data["div"], 2.0)
        self.assertIsNone(data["div_zero"])
        self.assertIsNone(data["div2_zero"])
        self.assertEqual(data["div2_type"], "type")
        self.assertEqual(data["nested"], "inner")

    def test_03_fallback_functions_work_at_runtime(self):
        data = run_worker("fallback")
        self.assertEqual(data["gen"], [0, 1, 2])
        self.assertEqual(data["uses_global"], 1)
        self.assertEqual(data["closure"], 2)