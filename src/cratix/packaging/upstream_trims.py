"""裁剪表：哪些模块/包不进包（沿用现有 Nuitka 构建的裁剪口径）。

来源：scripts/build.ps1 里的 --nofollow-import-to / --noinclude-dlls 参数。
"""

from __future__ import annotations

# 桌面端（PUBLIC）不包含的模块前缀/模块名
EXCLUDED_MODULES: tuple[str, ...] = (
    "common.storage",
    "common.services.executor",
    "sqlalchemy",
    "aiosqlite",
    "numpy",
    "asyncmy",
    "mysqlclient",
    "MySQLdb",
    "pycryptodomex",
    "Cryptodome",
    "PySide6.QtPdf",
    "PySide6.QtNetwork",
    "_pyrepl",
)

# 标准库里**用不到**的顶层模块 ✅（不进包 ✅ —— 直接省体积 ✅）
# tkinter ✅ 带 Tcl/Tk DLL ✅（~10 MB ✅）—— 客户端不用 ✅
EXCLUDED_STDLIB_MODULES: tuple[str, ...] = (
    "tkinter",
    "test",
    "idlelib",
    "ensurepip",
    "pydoc_data",
    "venv",
    "turtledemo",
    # **构建/测试期工具链 ✅ ✅** —— 运行时零引用 ✅ ✅（实测 grep ✅ ✅）✅
    # Cython ✅ 自带两套 ✅ ✅（`Cython` ✅ 大写 ✅ + `cython` ✅ 小写 ✅ ✅）✅
    "cython",
    "Cython",
    "setuptools",
    "pkg_resources",
    "pip",
    "wheel",
    "_pytest",
    "pytest",
    "pygments",
    "rich",
    "typer",
    "_pyrepl",
    "_ctypes_test",
    "_remote_debugging",
)

# 标准库的 C 扩展里用不到的 ✅（_tkinter ✅ → 顺带不带 Tcl/Tk DLL ✅）
# ⚠ **必须与实际使用口径一致** ✅ —— 少了会在运行期 ImportError ✅（实测 ✅）。
#   _sqlite3 ✅ → 顺带不带 sqlite3.dll ✅（-0.7 MiB ✅ 客户端不走本地库 ✅）；
#   _bz2 / winsound / _zoneinfo ✅ → 对应模块无人 import ✅；
#   _ctypes_test / _remote_debugging ✅ → 开发期工具 ✅（§15.4 同款 ✅）。
EXCLUDED_STDLIB_EXTENSIONS: tuple[str, ...] = (
    "_tkinter",
    "_sqlite3",
    "_bz2",
    "winsound",
    "_zoneinfo",
    "_ctypes_test",
    "_remote_debugging",
)

# 不进包的第三方包（整体排除，服务端依赖）
EXCLUDED_PACKAGES: tuple[str, ...] = (
    "fastapi",
    "starlette",
    "uvicorn",
    "jinja2",
    "jwt",
    "pyjwt",
    "asyncmy",
    "sqlalchemy",
    "aiosqlite",
    "opentelemetry",
    "uvloop",
    # **构建/测试期工具链 ✅ ✅** —— 运行时零引用 ✅ ✅（实测 grep ✅ ✅ client/common/server ✅ ✅）✅
    # 探针 ✅ ✅ 会验证 ✅ ✅「裁掉之后 ✅ ✅ 导入链 ✅ ✅ 仍 ✅ ✅ 完整 ✅ ✅」✅ ✅ ✅ ✅
    "pytest",
    "_pytest",
    "setuptools",
    "pkg_resources",
    "pip",
    "wheel",
    "pygments",
    "rich",
    "typer",
    # Cython ✅✅ 自带两套 ✅✅（大写 ✅ `Cython` ✅ + 小写 ✅ `cython` ✅ ✅）✅ —— 共 ~17 MB ✅✅
    # **构建期工具 ✅ ✅** ✅（编译 ✅✅ 在构建期 ✅✅ 完成 ✅✅ → 产物 ✅✅ 不需要 ✅✅ ✅ ✅）✅
    "cython",
    "Cython",
)

# Qt 只保留最小集（实测客户端只用 Core/Gui/Widgets ✅ 插件清单已并入 NATIVE_IN_PACKAGE ✅）
QT_KEEP_PLUGINS: tuple[str, ...] = (
    "platforms/qwindows.dll",
    "styles/qmodernwindowsstyle.dll",
)

# 原生 DLL：进包（随 exe 内存加载）
NATIVE_IN_PACKAGE: tuple[str, ...] = (
    "python314.dll",
    "python3.dll",
    "PySide6/QtCore.pyd",
    "PySide6/QtGui.pyd",
    "PySide6/QtWidgets.pyd",
    "PySide6/pyside6.abi3.dll",
    "PySide6/shiboken6.abi3.dll",
    "Qt6Core.dll",
    "Qt6Gui.dll",
    "Qt6Widgets.dll",
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/styles/qmodernwindowsstyle.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140_2.dll",
    # **zlib1.dll ✅** —— stub 的 DEFLATE 解压器 ✅（载荷压缩 ✅ 靠它 ✅）
    # 只依赖 api-ms-win-crt-* + KERNEL32 ✅ → 无静态 TLS ✅ → memdl 映射安全 ✅
    "zlib1.dll",
)

# 原生 DLL：只从 System32 取，不进包
SYSTEM_ONLY_DLLS: frozenset[str] = frozenset(
    {
        "kernel32.dll",
        "user32.dll",
        "gdi32.dll",
        "advapi32.dll",
        "ole32.dll",
        "oleaut32.dll",
        "shell32.dll",
        "shlwapi.dll",
        "imm32.dll",
        "dwmapi.dll",
        "setupapi.dll",
        "wtsapi32.dll",
        "comdlg32.dll",
        "d3d9.dll",
        "d3d11.dll",
        "d3d12.dll",
        "dxgi.dll",
        "dwrite.dll",
        "uxtheme.dll",
        "authz.dll",
        "mpr.dll",
        "bcryptprimitives.dll",
        "mscms.dll",
        "propsys.dll",
        "uiautomationcore.dll",
        "ws2_32.dll",
        "bcrypt.dll",
        "iphlpapi.dll",
        "ntdll.dll",
        "version.dll",
        "crypt32.dll",
        "secur32.dll",
        "winmm.dll",
        "winspool.drv",
        "combase.dll",
        "rpcrt4.dll",
        "netapi32.dll",
        "userenv.dll",
        "icuuc.dll",
    }
)

# 稳定 ABI 别名：python3.dll 是转发器，转发解析要求 OS 加载器能按名找到 python3xx.dll，
# 而手工映射的模块不在加载器模块表里 → 直接用已映射的 python314.dll 的导出表解析（见 §6.2）
STABLE_ABI_ALIASES: dict[str, str] = {"python3.dll": "python314.dll"}


def is_excluded_module(name: str) -> bool:
    for prefix in EXCLUDED_MODULES:
        if name == prefix or name.startswith(prefix + "."):
            return True
    return False


def is_excluded_package(top: str) -> bool:
    return top in EXCLUDED_PACKAGES
