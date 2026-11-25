"""平台接口（M6 ✅）。

**为什么要有这一层** ✅：cratix 的**发布链路本身是通用的** ✅
（依赖闭包 ✅ / 载荷容器 ✅ / 可复现构建 ✅ / AOT 的 IR 与 C 生成 ✅），
真正**跟平台绑死**的只有四件事 ✅：

1. **工具链** ✅：怎么找到编译器 ✅、怎么编 C ✅（Windows = MSVC ✅）；
2. **产物形态** ✅：可执行后缀 ✅（.exe/.bin ✅）、扩展后缀 ✅（.pyd/.so/.dylib ✅）；
3. **系统库判定** ✅：哪些 DLL/so 不用进载荷 ✅；
4. **运行期校验** ✅：零落盘 ✅ / 无标准句柄启动 ✅ / 环境净化 ✅。

**Windows 的实现完全走这些方法** ✅ —— 调用方（pipeline / cli / driver）只认接口 ✅，
不再直接 import `stubbuild` / `verify` / `sysdll` ✅（有测试守这条 ✅）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PlatformUnsupported(RuntimeError):
    """该平台还没有实现 ✅ —— 调用方应把它当成**明确的"不支持"** ✅，而不是构建失败 ✅。"""


@dataclass
class Toolchain:
    """工具链身份 ✅（用于可复现与增量 ✅）。"""

    name: str                 # "msvc" / "gcc" / "clang" / ...
    identity: str             # 版本或指纹字符串 ✅
    runner: str = ""          # 运行命令前缀（Windows 的 vcvars 调用 ✅）


class Platform:
    """平台接口 ✅ —— 子类实现 ✅。"""

    name: str = ""
    exe_suffix: str = ""
    extension_suffix: str = ""
    #: 原生库后缀 ✅（Windows = dll/pyd ✅，POSIX = so/dylib ✅）
    native_library_suffixes: tuple = ()

    # ---------- 1. 工具链 ✅ ----------
    def find_toolchain(self) -> Toolchain:
        raise PlatformUnsupported(self.unsupported())

    def run_command(self, command: str, cwd: Path | None = None) -> str:
        raise PlatformUnsupported(self.unsupported())

    def build_extension(self, c_path: Path, out_path: Path, python_prefix: Path) -> None:
        raise PlatformUnsupported(self.unsupported())

    def build_stub(self, build_dir: Path, console: bool = False, icon: Path | None = None) -> Path:
        raise PlatformUnsupported(self.unsupported())

    # ---------- 2. 系统库 ✅ ----------
    def is_system_library(self, name: str) -> bool:
        return False

    def resolve_library_alias(self, name: str) -> str:
        return name

    # ---------- 3. 运行期校验 ✅ ----------
    def zero_disk_check(self, exe: Path) -> dict:
        raise PlatformUnsupported(self.unsupported())

    def no_console_check(self, exe: Path) -> dict:
        raise PlatformUnsupported(self.unsupported())

    def sanitized_env(self, extra: dict | None = None) -> dict:
        raise PlatformUnsupported(self.unsupported())

    # ---------- 说明 ✅ ----------
    def unsupported(self) -> str:
        return (
            "cratix 还没有实现平台 " + (self.name or "?") + " 的发布链路 ✅；"
            "当前完整实现的是 Windows（MSVC + PE + memdl 引导）✅。"
            "接口定义见 src/cratix/platforms/base.py 与 docs/PLAN.md 的 M6 ✅。"
        )
