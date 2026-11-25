"""AOT 编译器的端到端测试 ✅。

核心：**真的编成 .pyd** ✅（MSVC ✅）然后与源码版**行为一致** ✅。
需要 MSVC ✅（没有则跳过 ✅）。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler import driver, loader
from cratix.compiler.golden import diff

CORPUS = ROOT / "tests" / "corpus"


def _have_msvc() -> bool:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    return vswhere.exists()


@unittest.skipUnless(_have_msvc(), "需要 MSVC 工具链")
class AotBuildTest(unittest.TestCase):

    def _build(self, filename: str, module_name: str):
        path = CORPUS / filename
        mc = driver.compile_module(path, module_name)
        self.assertTrue(mc.module_ok, mc.module_reason)
        # 产物放在 build/ 下 ✅ —— 加载后的 .pyd 在 Windows 上是锁住的 ✅
        # → 临时目录清理会失败 ✅（实测 ✅）→ 用固定的构建目录 ✅
        out_dir = ROOT / "build" / "aot_test"
        pyd = driver.build_pyd(mc, out_dir)
        # 用 **ctypes 直接调用 init 符号** ✅ —— 与 stub 的加载路径一致 ✅
        # （标准扩展加载器要求单阶段模块带 m_def ✅ —— 3.14 未导出 SetModuleDef ✅）
        module = loader.load_compiled_module(pyd, mc.init_symbol)
        return mc, module

    def test_01_corpus_basic_compiles_and_matches(self):
        mc, aot = self._build("corpus_basic.py", "cratix_corpus_basic")
        self.assertTrue(mc.supported_names, "没有函数被 AOT 化")
        source = diff.load_module(CORPUS / "corpus_basic.py", "cratix_src_corpus_basic")
        # 只比对**被 AOT 化**的函数 ✅（回退的函数不在产物里 ✅）
        calls = {
            "add": ((3, 4),),
            "mixed": ((1, 5, 9, 9),),
            "control": ((7,),),
            "strings": (("bob", 3),),
        }
        for name in mc.supported_names:
            self.assertIn(name, calls, "缺少 " + name + " 的调用样例")
            for args in calls[name]:
                self.assertEqual(
                    getattr(source, name)(*args),
                    getattr(aot, name)(*args),
                    name + " 的 AOT 结果与源码不一致",
                )
        # 被拒绝的函数不进产物 ✅（containers 用星号解包 ✅）
        self.assertIn("containers", mc.fallback_names)
        self.assertFalse(hasattr(aot, "containers"))

    def test_02_unsupported_functions_are_planned_as_fallback(self):
        mc = driver.compile_module(CORPUS / "corpus_unsupported.py", "cratix_corpus_unsupported")
        self.assertTrue(mc.module_ok, mc.module_reason)
        self.assertEqual(sorted(mc.fallback_names), ["closure", "gen", "uses_global"])
        for plan in mc.plans:
            self.assertFalse(plan.supported)
            self.assertTrue(plan.reason)