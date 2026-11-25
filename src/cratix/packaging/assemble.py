"""把 stub.exe 与 zip 载荷拼成最终单文件 exe。

结构：[stub.exe][zip 载荷][cookie 32B]
cookie = magic "CRTX1" + payload_offset(u64) + payload_size(u64) + crc32(u32) + reserved(u32)
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from pathlib import Path

COOKIE_MAGIC = b"CRTX1\x00\x00\x00"
COOKIE_SIZE = 32


def assemble(stub_exe: Path, payload: Path, out_exe: Path) -> Path:
    stub_bytes = stub_exe.read_bytes()
    zip_bytes = payload.read_bytes()
    # **输入也要校验** ✅：非 PE 的 stub / 非 ZIP 的载荷拼出来的产物必然不可启动 ✅，
    # 旧行为照样写文件并返回成功 ✅（单测：stub=b"NOTPE" → 产物以 b"NOTPEPK\x03" 开头 ✅）。
    if len(stub_bytes) < 2 or stub_bytes[:2] != b"MZ":
        raise SystemExit(f"[cratix] 拼装失败：stub 不是有效 PE：{stub_exe}")
    if len(zip_bytes) < 4 or zip_bytes[:2] != b"PK":
        raise SystemExit(f"[cratix] 拼装失败：载荷不是有效 zip：{payload}")
    # **载荷必须含 stub 需要的两个条目**：直接调用 assemble 时也不能拼出必然起不来的 exe。
    import zipfile as _zipfile

    try:
        with _zipfile.ZipFile(payload) as _zf:
            _names = set(_zf.namelist())
    except _zipfile.BadZipFile as exc:
        raise SystemExit(f"[cratix] 拼装失败：载荷 zip 无法读取：{payload}（{exc}）") from exc
    _missing = {"entry.txt", "meta.json"} - _names
    if _missing:
        raise SystemExit(f"[cratix] 拼装失败：载荷缺少条目 {sorted(_missing)}：{payload}")
    cookie = COOKIE_MAGIC + struct.pack("<QQII", len(stub_bytes), len(zip_bytes), zlib.crc32(zip_bytes), 0)
    assert len(cookie) == COOKIE_SIZE
    out_exe.parent.mkdir(parents=True, exist_ok=True)
    # **原子替换** ✅：旧实现直接 write_bytes 到最终路径 ✅ ——
    # 写到一半失败（磁盘满 / 杀毒软件拦截 / 目标被占用 ✅）
    # 会留下**截断的 exe** ✅（不可启动 ✅，且旧产物也没了 ✅）。
    # → 先写同目录临时文件 ✅ → os.replace 原子覆盖 ✅（失败时旧 exe 完好 ✅）。
    # tmp 名唯一 + xb ✅：预置的 .tmp hardlink/symlink 不能被跟随 ✅
    # （A225/A226/A255 同类 ✅）。
    tmp = out_exe.with_name(f"{out_exe.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with open(tmp, "xb") as handle:
            handle.write(stub_bytes + zip_bytes + cookie)
        if out_exe.is_symlink() or (hasattr(out_exe, "is_junction") and out_exe.is_junction()):
            try:
                if out_exe.is_dir():
                    out_exe.rmdir()
                else:
                    out_exe.unlink()
            except OSError:
                pass
        elif out_exe.is_dir():
            import shutil as _sh9

            _sh9.rmtree(out_exe, ignore_errors=True)
        os.replace(tmp, out_exe)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return out_exe
