"""产物验收：零落盘复检 / 无标准句柄启动 / 冒烟运行。

三个检查各自独立、可组合，CLI 的 `cratix verify` 按开关调用 ✅。

安全护栏（沿用上游教训 ✅）：
    - 临时目录不许是 exe 目录或其祖先（否则清理会误删产物 ✅）；
    - 临时目录不许是符号链接/目录联接（否则 rmtree 会顺着链接删 ✅）；
    - 临时目录只允许本工具自己的文件（否则清理会误删用户数据 ✅）；
    - 环境消毒：清掉 CRATIX_ENTRY/CODE/INTERP/LOG/DEBUG（否则验错对象 ✅）。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

SMOKE_MARKER = "cratix smoke ok"

_SANITIZE_KEYS = (
    "CRATIX_ENTRY",
    "CRATIX_CODE",
    "CRATIX_INTERP",
    "CRATIX_LOG",
    "CRATIX_DEBUG",
    "CRATIX_NOVFS",
    "CRATIX_VFS_CRT",
    "CRATIX_QT_CACHE",
)


def sanitized_env(extra: dict | None = None) -> dict:
    """环境消毒 + 追加。"""
    env = dict(os.environ)
    for key in _SANITIZE_KEYS:
        env.pop(key, None)
    env["CRATIX_NO_ERRORBOX"] = "1"
    if extra:
        env.update(extra)
    return env


def _prepare_tmp(exe: Path) -> Path:
    """准备独立临时目录（含安全护栏）。"""
    tmp = Path(
        os.environ.get("CRATIX_VERIFY_TMP")
        or (Path(tempfile.gettempdir()) / f"cratix_verify_{os.getpid()}")
    )
    tmp_res = tmp.resolve()
    exe_res = exe.resolve()
    if tmp_res == exe_res.parent or exe_res.is_relative_to(tmp_res):
        raise RuntimeError("CRATIX_VERIFY_TMP 不能是 exe 目录或其祖先目录（会误删产物）")
    if tmp.is_symlink() or (hasattr(tmp, "is_junction") and tmp.is_junction()):
        raise RuntimeError(f"CRATIX_VERIFY_TMP 不能是符号链接/目录联接：{tmp}")
    if tmp.exists() and not tmp.is_dir():
        raise RuntimeError(f"CRATIX_VERIFY_TMP 指向的不是目录：{tmp}")
    if tmp.exists():
        for item in tmp.iterdir():
            if item.name == "stub.log" and item.is_file() and not item.is_symlink():
                continue
            raise RuntimeError(f"CRATIX_VERIFY_TMP 里有非本工具文件 {item.name}（会误删）")
    tmp.mkdir(parents=True, exist_ok=True)
    for item in sorted(tmp.rglob("*"), reverse=True):
        link = item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction())
        try:
            if link:
                item.unlink()
            elif item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
        except OSError:
            pass
    return tmp


def _snapshot(root: Path) -> list[str]:
    """目录内容快照（相对路径 ✅，含目录与链接 ✅）。"""
    return sorted(
        str(x.relative_to(root)).replace("\\", "/")
        for x in root.rglob("*")
        if x.is_file() or x.is_dir() or x.is_symlink()
    )


def zero_disk_check(exe: Path, timeout: int = 120) -> dict:
    """独立 TEMP 目录 before/after —— 运行期零落盘复检。

    返回：{"ok", "zero_disk", "smoke_ok", "exit_code", "new_files", "stdout", "stderr", "tmp"}
    """
    exe = Path(exe).resolve()
    tmp = _prepare_tmp(exe)
    before = _snapshot(tmp)
    env = sanitized_env(
        {
            "TEMP": str(tmp),
            "TMP": str(tmp),
            "CRATIX_LOG": str(tmp / "stub.log"),
            "CRATIX_ENTRY": "cratix_smoke:run",
        }
    )
    try:
        proc = subprocess.run(
            [str(exe)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "zero_disk": False,
            "smoke_ok": False,
            "exit_code": None,
            "new_files": [],
            "runtime_files": [],
            "stdout": "",
            "stderr": "timeout",
            "tmp": str(tmp),
        }
    after = _snapshot(tmp)
    new_files = [n for n in after if n not in before]
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    log_text = ""
    log = tmp / "stub.log"
    try:
        if log.is_file():
            log_text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    # 运行期文件：只允许 stub.log（诊断口子 ✅）
    runtime_new = [
        n
        for n in new_files
        if not (n == "stub.log" and log.is_file() and not log.is_symlink())
    ]
    zero_disk = not runtime_new
    smoke_ok = proc.returncode == 0 and SMOKE_MARKER in (stdout + stderr + log_text)
    result = {
        "ok": zero_disk and smoke_ok,
        "zero_disk": zero_disk,
        "smoke_ok": smoke_ok,
        "exit_code": proc.returncode,
        "new_files": new_files,
        "runtime_files": runtime_new,
        "stdout": stdout,
        "stderr": stderr,
        "tmp": str(tmp),
    }
    if result["ok"] and not os.environ.get("CRATIX_VERIFY_TMP"):
        shutil.rmtree(tmp, ignore_errors=True)
    return result


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def no_console_check(exe: Path, wait_ms: int = 60000) -> dict:
    """无标准句柄启动（= Explorer 双击 ✅）—— 验证 GUI 子系统不会崩。

    返回：{"ok", "exit_code", "pid"}
    """
    exe = Path(exe).resolve()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    startup = _STARTUPINFO()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
    startup.hStdInput = None
    startup.hStdOutput = None
    startup.hStdError = None
    info = _PROCESS_INFORMATION()
    env = sanitized_env({"CRATIX_ENTRY": "cratix_smoke:run"})
    block = "".join(f"{k}={v}" + chr(0) for k, v in env.items()) + chr(0)
    ok = kernel32.CreateProcessW(
        None,
        ctypes.create_unicode_buffer(str(exe)),
        None,
        None,
        False,
        0x08000000 | 0x00000400,  # CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
        ctypes.create_unicode_buffer(block),
        None,
        ctypes.byref(startup),
        ctypes.byref(info),
    )
    if not ok:
        raise RuntimeError(f"CreateProcessW 失败：{ctypes.get_last_error()}")
    if wait_ms > 0:
        kernel32.WaitForSingleObject(info.hProcess, wait_ms)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hThread)
    kernel32.CloseHandle(info.hProcess)
    return {"ok": int(code.value) == 0, "exit_code": int(code.value), "pid": int(info.dwProcessId)}