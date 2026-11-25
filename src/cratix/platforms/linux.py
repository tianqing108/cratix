"""Linux 平台实现 ✅（**编译路径已实现** ✅ / 打包引导器仍待做 ⬜）。

**已实现** ✅：
- 工具链发现 ✅（gcc / cc / clang ✅）
- 扩展模块编译 ✅（`-shared -fPIC` ✅ + `-I<prefix>/include/python3.X` ✅）
- 系统库判定 ✅（libc / libm / libdl / libpthread / ld-linux ✅）
- 运行期校验 ✅（独立 TMPDIR 的零落盘复检 ✅ / 脱离会话的无标准句柄启动 ✅）

**未实现** ⬜（诚实标注 ✅）：
- **引导器** ✅：Windows 用 `memdl`（PE 内存加载 ✅）；Linux 要换成
  `memfd_create` + `dlopen("/proc/self/fd/N")` ✅ —— 还没写 ✅，所以 `build_stub` **明确报不支持** ✅
  （而不是悄悄出错 ✅）。

因此 ✅：Linux 上现在**可以编译扩展模块** ✅，但**还不能打完整产物** ✅。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Platform, PlatformUnsupported, Toolchain

#: Linux 上不用进载荷的系统库 ✅（按前缀匹配 ✅ —— 版本号会变 ✅）
_SYSTEM_LIBRARY_PREFIXES = (
    "libc.so",
    "libm.so",
    "libdl.so",
    "libpthread.so",
    "librt.so",
    "libutil.so",
    "libresolv.so",
    "libgcc_s.so",
    "libstdc++.so",
    "ld-linux",
    "linux-vdso.so",
    "ld64.so",
)


def _snapshot(root: Path) -> set:
    """目录里所有文件的路径集合 ✅（用于零落盘复检 ✅）。"""
    if not root.is_dir():
        return set()
    return {p for p in root.rglob("*") if p.is_file()}


class LinuxPlatform(Platform):
    name = "linux"
    exe_suffix = ""
    extension_suffix = ".so"
    native_library_suffixes = (".so",)

    # ---------- 1. 工具链 ✅ ----------
    def _candidates(self) -> list:
        return ["gcc", "cc", "clang"]

    def find_toolchain(self) -> Toolchain:
        for name in self._candidates():
            path = shutil.which(name)
            if not path:
                continue
            try:
                proc = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, timeout=30
                )
            except (OSError, subprocess.SubprocessError):
                continue
            identity = (proc.stdout or "").strip().splitlines()
            return Toolchain(
                name=Path(path).stem,
                identity=identity[0] if identity else "unknown",
                runner=path,
            )
        raise PlatformUnsupported(
            "找不到 C 编译器 ✅（试过 " + ", ".join(self._candidates()) + " ✅）；"
            "Linux 上需要 gcc 或 clang ✅。"
        )

    def run_command(self, command: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-1500:]
            raise RuntimeError(
                "命令失败（退出码 " + str(proc.returncode) + "）：" + command + chr(10) + tail
            )
        return (proc.stdout or "") + (proc.stderr or "")

    def build_extension(self, c_path: Path, out_path: Path, python_prefix: Path) -> None:
        toolchain = self.find_toolchain()
        prefix = Path(python_prefix)
        include = prefix / "include" / ("python%d.%d" % (sys.version_info.major, sys.version_info.minor))
        if not include.is_dir():
            include = prefix / "include"
        command = " ".join([
            '"' + toolchain.runner + '"',
            "-shared -fPIC -O2 -fno-strict-aliasing",
            "-o", '"' + str(out_path) + '"',
            '"' + str(c_path) + '"',
            '-I"' + str(include) + '"',
            # **不链 libpython** ✅ —— 扩展模块的符号由解释器提供 ✅（Linux 标准做法 ✅）
        ])
        self.run_command(command)
        if not Path(out_path).is_file():
            raise RuntimeError("未生成扩展模块：" + str(out_path))

    def build_stub(self, build_dir: Path, console: bool = False, icon: Path | None = None) -> Path:
        raise PlatformUnsupported(
            "Linux 的引导器还没实现 ✅：Windows 用 memdl（PE 内存加载 ✅），"
            "Linux 需要 memfd_create + dlopen('/proc/self/fd/N') ✅。"
            "扩展模块编译已经能用 ✅，但还不能打完整产物 ✅。"
        )

    # ---------- 2. 系统库 ✅ ----------
    def is_system_library(self, name: str) -> bool:
        low = Path(name).name.lower()
        return any(low.startswith(prefix) for prefix in _SYSTEM_LIBRARY_PREFIXES)

    def resolve_library_alias(self, name: str) -> str:
        # Linux 没有 Windows 那种"稳定别名 -> 带版本"的重定向 ✅
        return name

    # ---------- 3. 运行期校验 ✅ ----------
    def zero_disk_check(self, exe: Path) -> dict:
        """**独立 TMPDIR** ✅ 跑一遍 ✅ -> 看有没有新文件 ✅（与 Windows 同一口径 ✅）。"""
        exe = Path(exe)
        with tempfile.TemporaryDirectory(prefix="cratix-zero-") as tmp:
            before = _snapshot(Path(tmp))
            env = self.sanitized_env({"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp})
            proc = subprocess.run(
                [str(exe)], env=env, capture_output=True, text=True, timeout=120
            )
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
        """**脱离会话 + 关掉标准句柄** ✅（相当于桌面双击 ✅）。"""
        proc = subprocess.run(
            [str(exe)],
            env=self.sanitized_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            timeout=120,
        )
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode}

    def sanitized_env(self, extra: dict | None = None) -> dict:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        return env

    # ---------- 说明 ✅ ----------
    def unsupported(self) -> str:
        return (
            "cratix 在 Linux 上**编译扩展模块**已经可用 ✅，"
            "但**打包引导器**还没实现 ✅（memfd + dlopen ✅）。"
            "见 src/cratix/platforms/linux.py 顶部说明 ✅。"
        )
