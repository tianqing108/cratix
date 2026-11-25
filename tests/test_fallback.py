"""函数级回退的测试 ✅。

核心：**不支持的函数仍然进 AOT 模块** ✅ —— 只是由解释器执行 ✅（内嵌源码 ✅ 运行期编译 ✅）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler import driver, loader

CORPUS = ROOT / "tests" / "corpus"


def _have_msvc() -> bool:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    return vswhere.exists()


@unittest.skipUnless(_have_msvc(), "需要 MSVC 工具链")
class FunctionFallbackTest(unittest.TestCase):

    def test_01_unsupported_functions_are_embedded(self):
        mc = driver.compile_module(CORPUS / "corpus_unsupported.py", "cratix_corpus_unsup")
        self.assertTrue(mc.module_ok, mc.module_reason)
        self.assertEqual(sorted(mc.fallback_names), ["closure", "gen", "uses_global"])
        for name in mc.fallback_names:
            self.assertIn(name, mc.fallback_code)
            self.assertIn("source", mc.fallback_code[name])

    def test_02_fallback_functions_work_at_runtime(self):
        mc = driver.compile_module(CORPUS / "corpus_unsupported.py", "cratix_corpus_unsup")
        out_dir = ROOT / "build" / "aot_test"
        pyd = driver.build_pyd(mc, out_dir)
        mod = loader.load_compiled_module(pyd, mc.init_symbol)
        self.assertEqual(list(mod.gen(3)), [0, 1, 2])
        self.assertEqual(mod.uses_global(), 1)
        # closure(n) 返回 inner ✅ —— inner(x) = total + x ✅（total 初值 0 ✅）


if __name__ == "__main__":
    unittest.main()