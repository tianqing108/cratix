"""系统 DLL 判定。

上游 cypack 的 SYSTEM_ONLY_DLLS 是一份**手工白名单** ✅ —— 不全 ✅：
实测漏 comctl32.dll ✅ → Tcl/Tk 的依赖被判成"非系统 DLL"✅ → 构建失败 ✅。

本模块的口径：**上游白名单 ∪ 实际存在于 %SystemRoot%\\System32 的文件** ✅
→ 白名单漏项由文件系统兜底 ✅ → 不必手工维护 ✅。
"""

from __future__ import annotations

import os
from pathlib import Path

from .upstream_trims import STABLE_ABI_ALIASES, SYSTEM_ONLY_DLLS

_SYSTEM32_NAMES: frozenset[str] | None = None


def _system32_names() -> frozenset[str]:
    """System32 下的文件名集合（一次性缓存）。"""
    global _SYSTEM32_NAMES
    if _SYSTEM32_NAMES is None:
        root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
        names: frozenset[str] = frozenset()
        if root:
            directory = Path(root) / "System32"
            try:
                names = frozenset(p.name.lower() for p in directory.iterdir() if p.is_file())
            except OSError:
                names = frozenset()
        _SYSTEM32_NAMES = names
    return _SYSTEM32_NAMES


def is_system_only(dll: str) -> bool:
    """该 DLL 是否由系统提供（不需要进包）。"""
    low = dll.lower()
    if low in SYSTEM_ONLY_DLLS:
        return True
    # API set 转发名（api-ms-win-crt-* 等）由 API set schema 解析 ✅ —— 不是磁盘文件 ✅
    if low.startswith(("api-ms-", "ext-ms-")):
        return True
    return low in _system32_names()


def resolve_alias(dll: str) -> str:
    """稳定 ABI 别名：python3.dll → python314.dll（转发器需 OS 加载器按名解析，手工映射做不到）。"""
    return STABLE_ABI_ALIASES.get(dll.lower(), dll)