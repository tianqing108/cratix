"""原生清单：哪些 DLL 进包、哪些只走 System32、稳定 ABI 别名规则。

依据见 docs/PLAN.md §6.2（实测 PE 导入表推导）。
"""

from __future__ import annotations

from .defaults import NATIVE_IN_PACKAGE, STABLE_ABI_ALIASES, SYSTEM_ONLY_DLLS


def in_package_list() -> list[str]:
    return list(NATIVE_IN_PACKAGE)


def is_system_only(dll: str) -> bool:
    return dll.lower() in SYSTEM_ONLY_DLLS


def resolve_alias(dll: str) -> str:
    """稳定 ABI 别名：python3.dll → python314.dll（转发器需 OS 加载器按名解析，手工映射做不到）。"""
    return STABLE_ABI_ALIASES.get(dll.lower(), dll)
