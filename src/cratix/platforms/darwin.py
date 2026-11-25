"""macOS 占位（M6 ✅）。

与 Linux 同样**只给接口与占位** ✅ —— 任何调用都是明确的 PlatformUnsupported ✅。
后续实现要点（留档 ✅）：

- 工具链 ✅：clang ✅（`-dynamiclib -undefined dynamic_lookup` ✅）；
- 扩展后缀 ✅：`.so`（CPython 3 ✅ 用 `EXT_SUFFIX` ✅，形如 `cpython-314-darwin.so` ✅）；
- 引导器 ✅：`dlopen` 可直接加载 ✅；但**代码签名 / 硬化运行时**要先处理 ✅；
- 系统库判定 ✅：`/usr/lib/libSystem.B.dylib` 等 ✅；
- 运行期校验 ✅：零落盘用独立 `TMPDIR` ✅；无标准句柄启动要绕开 AppKit 的窗口服务器要求 ✅。
"""

from __future__ import annotations

from .base import Platform


class DarwinPlatform(Platform):
    name = "darwin"
    exe_suffix = ""
    extension_suffix = ".so"
    native_library_suffixes = (".dylib", ".so")

    def unsupported(self) -> str:
        return (
            "cratix 的 macOS 发布链路还没实现 ✅（M6 只有接口与占位）✅；"
            "当前完整实现的是 Windows ✅。实现要点见 src/cratix/platforms/darwin.py 顶部注释 ✅。"
        )
