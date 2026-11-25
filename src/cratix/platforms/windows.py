"""Windows 平台实现 ✅（MSVC / PE / memdl 引导 ✅）。

**这一层是薄壳** ✅：真正的实现在 `packaging/stubbuild.py` ✅、`packaging/verify.py` ✅、
`packaging/sysdll.py` ✅ —— 这里只做**接口转接** ✅。
好处 ✅：调用方只依赖接口 ✅，而已经验证过的实现不用动 ✅（风险最小 ✅）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .base import Platform, PlatformUnsupported, Toolchain


class WindowsPlatform(Platform):
    name = "windows"
    exe_suffix = ".exe"
    extension_suffix = ".pyd"
    native_library_suffixes = (".dll", ".pyd")

    # ---------- 1. 工具链 ✅ ----------
    def find_toolchain(self) -> Toolchain:
        from ..packaging import stubbuild

        try:
            vcvars = stubbuild.find_vcvars()
            identity = stubbuild.vcvars_identity()
        except Exception as exc:  # noqa: BLE001 - 找不到 MSVC 要变成**明确**的"不支持" ✅
            raise PlatformUnsupported(
                "找不到 MSVC 工具链 ✅（cratix 目前用 MSVC 编译 C 后端与引导器 ✅）：" + str(exc)
            ) from exc
        return Toolchain(name="msvc", identity=identity, runner=str(vcvars))

    def run_command(self, command: str, cwd: Path | None = None) -> str:
        from ..packaging import stubbuild

        return stubbuild.run_cmd(command, cwd)

    def build_extension(self, c_path: Path, out_path: Path, python_prefix: Path) -> None:
        from ..packaging import stubbuild

        vcvars = stubbuild.find_vcvars()
        include = Path(python_prefix) / "include"
        libs = Path(python_prefix) / "libs"
        lib_name = "python%d%d.lib" % (sys.version_info.major, sys.version_info.minor)
        stem = Path(out_path).stem
        out_dir = Path(out_path).parent
        cmd = (
            'call "' + str(vcvars) + '" >nul && cd /d "' + str(out_dir) + '" && '
            # **必须用 /MD** ✅（DLL CRT ✅）—— 与解释器同一个 CRT ✅；
            # /MT（静态 CRT ✅）会让解释器用 DLL CRT 释放我们的内存 ✅ -> 堆损坏 ✅。
            'cl /nologo /O2 /MD /W3 /utf-8 /c /Brepro "' + Path(c_path).name + '" /I"' + str(include) + '" '
            '&& link /DLL /Brepro /OUT:"' + stem + '.pyd" "' + stem + '.obj" '
            '/LIBPATH:"' + str(libs) + '" ' + lib_name
        )
        stubbuild.run_cmd(cmd)

    def build_stub(self, build_dir: Path, console: bool = False, icon: Path | None = None) -> Path:
        from ..packaging import stubbuild

        return stubbuild.build_stub(build_dir, console, icon)

    # ---------- 2. 系统库 ✅ ----------
    def is_system_library(self, name: str) -> bool:
        from ..packaging import sysdll

        return sysdll.is_system_only(name)

    def resolve_library_alias(self, name: str) -> str:
        from ..packaging import sysdll

        return sysdll.resolve_alias(name)

    # ---------- 3. 运行期校验 ✅ ----------
    def zero_disk_check(self, exe: Path) -> dict:
        from ..packaging import verify

        return verify.zero_disk_check(exe)

    def no_console_check(self, exe: Path) -> dict:
        from ..packaging import verify

        return verify.no_console_check(exe)

    def sanitized_env(self, extra: dict | None = None) -> dict:
        from ..packaging import verify

        return verify.sanitized_env(extra)
