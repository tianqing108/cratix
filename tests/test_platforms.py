"""平台接口测试 ✅（M6 ✅）。

守两件事 ✅：
1. **接口完整** ✅：Windows 实现了全部接口 ✅，其它平台是**明确的不支持** ✅；
2. **调用方只认接口** ✅：pipeline / cli / driver 不再直接依赖 Windows 专有模块 ✅。
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix import platforms
from cratix.platforms.base import Platform, PlatformUnsupported

#: 接口方法 -> 一个"随便什么参数"的调用 ✅（占位平台必须都抛 PlatformUnsupported ✅）
INTERFACE_CALLS = {
    "find_toolchain": lambda p: p.find_toolchain(),
    "run_command": lambda p: p.run_command("echo hi"),
    "build_extension": lambda p: p.build_extension(Path("a.c"), Path("a.pyd"), Path(".")),
    "build_stub": lambda p: p.build_stub(Path(".")),
    "zero_disk_check": lambda p: p.zero_disk_check(Path("a.exe")),
    "no_console_check": lambda p: p.no_console_check(Path("a.exe")),
    "sanitized_env": lambda p: p.sanitized_env({}),
}


class PlatformRegistryTest(unittest.TestCase):
    def test_supported_platforms_are_declared(self):
        self.assertEqual(platforms.supported_platforms(), ["darwin", "linux", "windows"])

    def test_current_matches_sys_platform(self):
        plat = platforms.current()
        self.assertEqual(plat.name, {"win32": "windows", "linux": "linux", "darwin": "darwin"}[sys.platform])

    def test_unknown_platform_is_unsupported(self):
        with self.assertRaises(PlatformUnsupported):
            platforms.platform_for("solaris")

    def test_platforms_are_cached(self):
        self.assertIs(platforms.platform_for("linux"), platforms.platform_for("linux"))


class WindowsPlatformTest(unittest.TestCase):
    def setUp(self):
        if sys.platform != "win32":
            self.skipTest("Windows 专有实现 ✅")
        self.plat = platforms.platform_for("windows")

    def test_suffixes(self):
        self.assertEqual(self.plat.exe_suffix, ".exe")
        self.assertEqual(self.plat.extension_suffix, ".pyd")
        self.assertIn(".dll", self.plat.native_library_suffixes)

    def test_system_library_classification(self):
        self.assertTrue(self.plat.is_system_library("kernel32.dll"))
        self.assertTrue(self.plat.is_system_library("USER32.DLL"))
        self.assertFalse(self.plat.is_system_library("python314.dll"))
        self.assertFalse(self.plat.is_system_library("zlib1.dll"))

    def test_library_alias(self):
        # 稳定 ABI 别名 ✅：python3.dll -> python314.dll ✅
        self.assertEqual(self.plat.resolve_library_alias("python3.dll").lower(), "python314.dll")

    def test_toolchain_is_available(self):
        toolchain = self.plat.find_toolchain()
        self.assertEqual(toolchain.name, "msvc")
        self.assertTrue(toolchain.runner)
        self.assertTrue(toolchain.identity)


class PlaceholderPlatformTest(unittest.TestCase):
    def test_placeholders_reject_every_interface_call(self):
        for name in ("linux", "darwin"):
            plat = platforms.platform_for(name)
            for method, call in INTERFACE_CALLS.items():
                with self.subTest(platform=name, method=method):
                    with self.assertRaises(PlatformUnsupported):
                        call(plat)

    def test_placeholders_have_shape(self):
        self.assertEqual(platforms.platform_for("linux").extension_suffix, ".so")
        self.assertEqual(platforms.platform_for("darwin").native_library_suffixes[0], ".dylib")


class InterfaceCompletenessTest(unittest.TestCase):
    def test_windows_implements_whole_interface(self):
        from cratix.platforms.windows import WindowsPlatform

        for name, _call in INTERFACE_CALLS.items():
            self.assertTrue(hasattr(WindowsPlatform, name), name + " 未实现")
            base = getattr(Platform, name)
            impl = getattr(WindowsPlatform, name)
            self.assertIsNot(base, impl, name + " 仍是基类占位实现")

    def test_callers_go_through_the_interface(self):
        """**验收线** ✅：Windows 实现完全走抽象接口 ✅（调用方不再直接依赖）✅。"""
        forbidden = ("stubbuild", "sysdll", "verify.")
        for relative in ("build/pipeline.py", "cli.py", "compiler/driver.py"):
            text = (SRC / "cratix" / relative).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(
                    "from ..packaging import" + " " + token,
                    text,
                    relative + " 仍在直接 import " + token,
                )
            self.assertNotIn("stubbuild.", text, relative + " 仍在直接调用 stubbuild")
            self.assertNotIn("sysdll.", text, relative + " 仍在直接调用 sysdll")
            self.assertNotIn("verify.zero_disk_check", text, relative + " 仍在直接调用 verify")
            self.assertIn("platforms.current()", text, relative + " 应当通过平台接口")


if __name__ == "__main__":
    unittest.main()
