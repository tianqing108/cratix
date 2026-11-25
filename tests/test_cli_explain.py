"""`cratix explain` ✅ —— 开发者工具：解释 AOT 覆盖率 ✅。"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix import cli

TOML = """[project]
name = "explaindemo"
version = "0.1.0"
entry = "demo.main:main"

[build]
targets = ["onefile"]
console = true

[compiler]
backend = "aot"
probe = "off"
"""

MAIN = '''"""有回退的模块 ✅：用来验 explain 的输出 ✅。"""


def clean(value):
    return value + 1


def generatorish(values):
    for item in values:
        yield item
'''


class ExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cratix_explain_"))
        (self.tmp / "demo").mkdir()
        (self.tmp / "demo" / "__init__.py").write_text("", encoding="utf-8")
        (self.tmp / "demo" / "main.py").write_text(MAIN, encoding="utf-8")
        (self.tmp / "cratix.toml").write_text(TOML, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv):
        buffer = io.StringIO()
        previous = Path.cwd()
        try:
            import os

            os.chdir(self.tmp)
            with contextlib.redirect_stdout(buffer):
                code = cli.main(["explain", *argv])
        finally:
            import os

            os.chdir(previous)
        return code, buffer.getvalue()

    def test_explain_lists_fallbacks(self):
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("demo.main", output)
        # **能 AOT 的函数** ✅ 与**回退的函数** ✅ 都要能看见 ✅
        self.assertIn("AOT", output)
        self.assertIn("generatorish", output, output)
        self.assertIn("生成器", output, output)
        self.assertIn("合计：", output)

    def test_explain_only_fallback(self):
        code, output = self._run("--only", "fallback")
        self.assertEqual(code, 0)
        self.assertIn("合计：", output)
        # **只列回退时不打原因表** ✅
        self.assertNotIn("回退原因 top", output)


if __name__ == "__main__":
    unittest.main()
