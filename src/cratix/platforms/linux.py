"""Linux 占位（M6 ✅）。

**只写接口该有的样子** ✅，不假装能用 ✅ —— 任何调用都会拿到**明确的** PlatformUnsupported ✅。
后续实现要点（留档 ✅）：

- 工具链 ✅：gcc/clang ✅（`-shared -fPIC` ✅、`libpython3.x.so` ✅）；
- 扩展后缀 ✅：`.so`（CPython 3 ✅ 用 `EXT_SUFFIX` ✅，形如 `cpython-314-x86_64-linux-gnu.so` ✅）；
- 引导器 ✅：不需要 memdl ✅ —— Linux 的 `dlopen` 可以直接从 `/proc/self/exe` 旁边或 memfd 加载 ✅；
- 系统库判定 ✅：`libc.so.6` / `libm.so.6` / `ld-linux-*` 等 ✅；
- 运行期校验 ✅：零落盘用独立的 `TMPDIR` ✅、无标准句柄启动用 `setsid` + 关闭 stdio ✅。
"""

from __future__ import annotations

from .base import Platform


class LinuxPlatform(Platform):
    name = "linux"
    exe_suffix = ""
    extension_suffix = ".so"
    native_library_suffixes = (".so",)

    def unsupported(self) -> str:
        return (
            "cratix 的 Linux 发布链路还没实现 ✅（M6 只有接口与占位）✅；"
            "当前完整实现的是 Windows ✅。实现要点见 src/cratix/platforms/linux.py 顶部注释 ✅。"
        )
