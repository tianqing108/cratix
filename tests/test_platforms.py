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


class LinuxPlatformTest(unittest.TestCase):
    """Linux ✅ —— **编译路径已实现** ✅（打包引导器还没有 ✅）。"""

    def setUp(self):
        self.plat = platforms.platform_for("linux")

    def test_suffixes(self):
        self.assertEqual(self.plat.exe_suffix, "")
        self.assertEqual(self.plat.extension_suffix, ".so")
        self.assertIn(".so", self.plat.native_library_suffixes)

    def test_system_libraries(self):
        for name in ("libc.so.6", "libm.so.6", "libpthread.so.0", "ld-linux-x86-64.so.2"):
            self.assertTrue(self.plat.is_system_library(name), name)
        for name in ("libpython3.14.so.1.0", "libz.so.1", "libcustom.so"):
            self.assertFalse(self.plat.is_system_library(name), name)

    def test_library_alias_is_identity(self):
        self.assertEqual(self.plat.resolve_library_alias("libz.so.1"), "libz.so.1")

    def test_build_stub_is_explicitly_unsupported(self):
        with self.assertRaises(PlatformUnsupported) as ctx:
            self.plat.build_stub(Path("."))
        self.assertIn("memfd", str(ctx.exception))

    def test_build_extension_command(self):
        """**不真编译** ✅ —— 只检查命令形状 ✅（Windows 上也能验证 ✅）。"""
        captured = {}
        self.plat.find_toolchain = lambda: __import__(
            "cratix.platforms.base", fromlist=["Toolchain"]
        ).Toolchain(name="gcc", identity="gcc 13", runner="/usr/bin/gcc")

        def fake_run(command, cwd=None):
            captured["command"] = command
            Path(captured_out).parent.mkdir(parents=True, exist_ok=True)
            Path(captured_out).write_text("", encoding="utf-8")
            return ""

        global captured_out
        with __import__("tempfile").TemporaryDirectory() as tmp:
            out = Path(tmp) / "mod.so"
            captured_out = str(out)
            self.plat.run_command = fake_run
            self.plat.build_extension(Path(tmp) / "mod.c", out, Path(tmp))
        command = captured["command"]
        self.assertIn("-shared -fPIC", command)
        self.assertIn("-I\"", command)
        self.assertIn(".so", command)

    def test_sanitized_env_keeps_it_minimal(self):
        env = self.plat.sanitized_env({"TMPDIR": "/tmp/x"})
        self.assertEqual(env["TMPDIR"], "/tmp/x")
        self.assertIn("PATH", env)


class DarwinPlatformTest(unittest.TestCase):
    """macOS ✅ —— 与 Linux 同构 ✅（编译路径已实现 ✅，引导器待做 ✅）。"""

    def setUp(self):
        self.plat = platforms.platform_for("darwin")

    def test_suffixes(self):
        self.assertEqual(self.plat.extension_suffix, ".so")
        self.assertIn(".dylib", self.plat.native_library_suffixes)

    def test_system_libraries(self):
        self.assertTrue(self.plat.is_system_library("/usr/lib/libSystem.B.dylib"))
        self.assertTrue(self.plat.is_system_library("libSystem.B.dylib"))
        self.assertTrue(self.plat.is_system_library("/usr/lib/libc++.1.dylib"))
        self.assertFalse(self.plat.is_system_library("/opt/homebrew/lib/libfoo.dylib"))

    def test_build_stub_is_explicitly_unsupported(self):
        with self.assertRaises(PlatformUnsupported) as ctx:
            self.plat.build_stub(Path("."))
        self.assertIn("代码签名", str(ctx.exception))

    def test_build_extension_command(self):
        from cratix.platforms.base import Toolchain

        captured = {}
        self.plat.find_toolchain = lambda: Toolchain(name="clang", identity="clang 16", runner="/usr/bin/clang")

        def fake_run(command, cwd=None):
            captured["command"] = command
            Path(captured["out"]).parent.mkdir(parents=True, exist_ok=True)
            Path(captured["out"]).write_text("", encoding="utf-8")
            return ""

        with __import__("tempfile").TemporaryDirectory() as tmp:
            out = Path(tmp) / "mod.so"
            captured["out"] = str(out)
            self.plat.run_command = fake_run
            self.plat.build_extension(Path(tmp) / "mod.c", out, Path(tmp))
        command = captured["command"]
        self.assertIn("-bundle -undefined dynamic_lookup", command)
        self.assertIn("-I\"", command)


class PlaceholderPlatformTest(unittest.TestCase):
    def test_placeholders_reject_every_interface_call(self):
        # **三个平台都有实现了** ✅ —— 只剩"未实现的方法"要明确拒绝 ✅
        for name in ("linux", "darwin"):
            plat = platforms.platform_for(name)
            with self.subTest(platform=name, method="build_stub"):
                with self.assertRaises(PlatformUnsupported):
                    plat.build_stub(Path("."))

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
