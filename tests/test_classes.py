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

class ClassSupportTest(unittest.TestCase):
    def test_01_class_is_compiled(self):
        data = run_worker("classes")
        self.assertEqual(data["classes"], ["Point"])
        self.assertEqual(data["kind"], "point")

    def test_02_instance_behavior(self):
        data = run_worker("classes")
        self.assertEqual(data["norm2"], 25)
        self.assertEqual(data["describe"], "point:3,4")
        self.assertEqual(data["shifted"], "point:4,4")
        self.assertEqual(data["shifted2"], "point:4,6")
        self.assertEqual(data["x"], 3)
        self.assertEqual(data["y"], 4)

    def test_03_type_identity(self):
        data = run_worker("classes")
        self.assertTrue(data["is_instance"])
        self.assertEqual(data["type_name"], "Point")