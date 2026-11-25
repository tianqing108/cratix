"""macOS 平台实现 ✅（**编译路径已实现** ✅ / 打包引导器仍待做 ⬜）。

与 Linux 同构 ✅，差别在：
- 编译器：`clang` ✅（`-dynamiclib -undefined dynamic_lookup` ✅ —— 符号由解释器提供 ✅）
- 扩展后缀：`.so` ✅（CPython 的 `EXT_SUFFIX` 也是 `.so` 结尾 ✅）
- 系统库：`/usr/lib/libSystem.B.dylib` ✅ / `libc++.1.dylib` ✅ / `libobjc` ✅
- 零落盘：独立 `TMPDIR` ✅；无句柄启动：`start_new_session` ✅（AppKit 需要窗口服务器 ✅ —— 那是 GUI 程序自己的事 ✅）

**未实现** ⬜：引导器 ✅（要 `dlopen` ✅ 且要处理**代码签名 / 硬化运行时** ✅）。
所以 `build_stub` **明确报不支持** ✅。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Platform, PlatformUnsupported, Toolchain

_SYSTEM_LIBRARY_PREFIXES = (
    "/usr/lib/libsystem",
    "/usr/lib/libc++",
    "/usr/lib/libobjc",
    "/usr/lib/libcups",
    "/usr/lib/libresolv",
    "/usr/lib/libz.",
    "/usr/lib/system/",
    "/usr/lib/libncurses",
    "/usr/lib/libiconv",
    "/usr/lib/libbz2",
)


def _snapshot(root: Path) -> set:
    if not root.is_dir():
        return set()
    return {p for p in root.rglob("*") if p.is_file()}


class DarwinPlatform(Platform):
    name = "darwin"
    exe_suffix = ""
    extension_suffix = ".so"
    native_library_suffixes = (".dylib", ".so")

    def _candidates(self) -> list:
        return ["clang", "cc"]

    def find_toolchain(self) -> Toolchain:
        for name in self._candidates():
            path = shutil.which(name)
            if not path:
                continue
            try:
                proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            lines = (proc.stdout or "").strip().splitlines()
            return Toolchain(name=Path(path).stem, identity=lines[0] if lines else "unknown", runner=path)
        raise PlatformUnsupported(
            "找不到 clang ✅（macOS 上装 Xcode Command Line Tools 即可 ✅）：xcode-select --install ✅"
        )

    def run_command(self, command: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd) if cwd is not None else None,
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-1500:]
            raise RuntimeError("命令失败（退出码 " + str(proc.returncode) + "）：" + command + chr(10) + tail)
        return (proc.stdout or "") + (proc.stderr or "")

    def build_extension(self, c_path: Path, out_path: Path, python_prefix: Path) -> None:
        toolchain = self.find_toolchain()
        prefix = Path(python_prefix)
        include = prefix / "include" / ("python%d.%d" % (sys.version_info.major, sys.version_info.minor))
        if not include.is_dir():
            include = prefix / "include"
        command = " ".join([
            '"' + toolchain.runner + '"',
            # **`-undefined dynamic_lookup`** ✅ —— Python 符号在运行时由解释器提供 ✅
            "-bundle -undefined dynamic_lookup -O2 -fno-strict-aliasing",
            "-o", '"' + str(out_path) + '"',
            '"' + str(c_path) + '"',
            '-I"' + str(include) + '"',
        ])
        self.run_command(command)
        if not Path(out_path).is_file():
            raise RuntimeError("未生成扩展模块：" + str(out_path))

    def build_stub(self, build_dir: Path, console: bool = False, icon: Path | None = None) -> Path:
        raise PlatformUnsupported(
            "macOS 的引导器还没实现 ✅（需要 dlopen ✅ 且要处理代码签名 / 硬化运行时 ✅）。"
            "扩展模块编译已经能用 ✅，但还不能打完整产物 ✅。"
        )

    def is_system_library(self, name: str) -> bool:
        low = name.lower()
        if any(low.startswith(prefix) for prefix in _SYSTEM_LIBRARY_PREFIXES):
            return True
        # 裸名字（只有 basename ✅）时按前缀判 ✅
        base = Path(low).name
        return base.startswith(("libsystem", "libc++", "libobjc"))

    def resolve_library_alias(self, name: str) -> str:
        return name

    def zero_disk_check(self, exe: Path) -> dict:
        exe = Path(exe)
        with tempfile.TemporaryDirectory(prefix="cratix-zero-") as tmp:
            before = _snapshot(Path(tmp))
            env = self.sanitized_env({"TMPDIR": tmp})
            proc = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=120)
            after = _snapshot(Path(tmp))
        new_files = sorted(str(p) for p in after - before)
        return {
            "ok": not new_files and proc.returncode == 0,
            "zero_disk": not new_files,
            "runtime_files": new_files,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
        }

    def no_console_check(self, exe: Path) -> dict:
        proc = subprocess.run(
            [str(exe)], env=self.sanitized_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, timeout=120,
        )
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode}

    def sanitized_env(self, extra: dict | None = None) -> dict:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        return env

    def unsupported(self) -> str:
        return (
            "cratix 在 macOS 上**编译扩展模块**已经可用 ✅，"
            "但**打包引导器**还没实现 ✅（dlopen + 代码签名 ✅）。"
        )
