"""`scripts/coverage_report.py` 的渲染测试 ✅（不扫标准库 ✅ —— 只验格式 ✅）。"""
from __future__ import annotations

import collections
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import coverage_report  # noqa: E402


class CoverageReportTest(unittest.TestCase):
    def test_render_contains_key_sections(self):
        data = {
            "total": 10,
            "ok": 9,
            "module_fallback": 1,
            "crashes": 0,
            "aot": 100,
            "fallback": 5,
            "module_reasons": collections.Counter({"模块级语句不支持：X": 1}),
            "func_reasons": collections.Counter({"生成器": 4, "async 方法": 1}),
            "with_fallback": [(4, "a/b.py")],
        }
        text = coverage_report.render(data)
        self.assertIn("# AOT 覆盖率报告", text)
        self.assertIn("| 扫描模块 | 10 |", text)
        self.assertIn("**0.952**", text)  # 100 / 105
        self.assertIn("生成器", text)
        self.assertIn("a/b.py", text)
        self.assertIn("回退 ≠ 错误", text)

    def test_render_handles_zero(self):
        data = {
            "total": 0, "ok": 0, "module_fallback": 0, "crashes": 0,
            "aot": 0, "fallback": 0,
            "module_reasons": collections.Counter(),
            "func_reasons": collections.Counter(),
            "with_fallback": [],
        }
        text = coverage_report.render(data)
        self.assertIn("**0.000**", text)


if __name__ == "__main__":
    unittest.main()
