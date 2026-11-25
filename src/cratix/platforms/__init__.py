"""平台选择 ✅（M6 ✅）。

`current()` 是**唯一**该被调用方 import 的入口 ✅：
拿到一个 Platform ✅，然后只调它的方法 ✅ —— 不关心底下是 MSVC 还是 gcc ✅。
"""

from __future__ import annotations

import sys

from .base import Platform, PlatformUnsupported, Toolchain

__all__ = [
    "Platform",
    "PlatformUnsupported",
    "Toolchain",
    "current",
    "platform_for",
    "supported_platforms",
]

_CACHE: dict = {}

# sys.platform -> 实现类 ✅（延迟 import ✅ —— 非本平台的依赖不该被拉进来 ✅）
def _registry():
    from .darwin import DarwinPlatform
    from .linux import LinuxPlatform
    from .windows import WindowsPlatform

    # **键是平台名** ✅（不是 sys.platform ✅ —— win32 只是 Windows 的 sys.platform 值 ✅）
    return {
        "windows": WindowsPlatform,
        "linux": LinuxPlatform,
        "darwin": DarwinPlatform,
    }


#: sys.platform -> 平台名 ✅
_SYS_TO_NAME = {
    "win32": "windows",
    "linux": "linux",
    "darwin": "darwin",
}


def supported_platforms() -> list:
    """有实现（哪怕是占位）的平台名 ✅。"""
    return sorted(cls().name for cls in _registry().values())


def platform_for(name: str) -> Platform:
    """按 `sys.platform` 风格的名字取平台对象 ✅（测试与交叉构建用 ✅）。"""
    if name in _CACHE:
        return _CACHE[name]
    registry = _registry()
    if name not in registry:
        raise PlatformUnsupported(
            "未知平台 " + name + " ✅；cratix 目前认识的平台：" + ", ".join(sorted(registry)) + " ✅"
        )
    instance = registry[name]()
    _CACHE[name] = instance
    return instance


def current() -> Platform:
    """当前平台 ✅（未知平台会拿到**明确的** PlatformUnsupported ✅）。"""
    return platform_for(_SYS_TO_NAME.get(sys.platform, sys.platform))
