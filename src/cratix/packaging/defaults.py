"""载荷裁剪与原生清单的**配置驱动**入口。

上游 cypack 的 trims.py 把裁剪表硬编码在代码里（且带 sports-server 的业务裁剪）。
cratix 把它拆成两部分：
    1. **通用安全表**（upstream_trims.py，vendor 而来）：系统 DLL 白名单、稳定 ABI 别名；
    2. **配置驱动表**（本模块）：模块/包/标准库裁剪与进包原生清单，默认空，由 cratix.toml 决定。

配置来源优先级：环境变量 CRATIX_CONFIG 指向的 toml -> 当前工作目录的 cratix.toml -> 内置默认值。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .upstream_trims import STABLE_ABI_ALIASES, SYSTEM_ONLY_DLLS

__all__ = [
    "EXCLUDED_MODULES",
    "EXCLUDED_STDLIB_MODULES",
    "EXCLUDED_STDLIB_EXTENSIONS",
    "EXCLUDED_PACKAGES",
    "QT_KEEP_PLUGINS",
    "NATIVE_IN_PACKAGE",
    "SYSTEM_ONLY_DLLS",
    "STABLE_ABI_ALIASES",
    "PROJECT_TOPS",
    "is_excluded_module",
    "is_excluded_package",
    "apply_policy",
    "load_policy",
]

# --- 配置驱动的默认表（默认空：通用工具不做业务裁剪） ---
EXCLUDED_MODULES = ()
EXCLUDED_STDLIB_MODULES = ()
EXCLUDED_STDLIB_EXTENSIONS = ()
EXCLUDED_PACKAGES = ()
QT_KEEP_PLUGINS = ()
PROJECT_TOPS = frozenset()

# 进包原生清单：解释器本体 + 前向器 + stub 自带解压器
# python3xx.dll 由构建解释器版本推导，避免硬编码版本号。
_PY_DLL = "python%d%d.dll" % (sys.version_info.major, sys.version_info.minor)
NATIVE_IN_PACKAGE = (
    _PY_DLL,
    "python3.dll",
    "zlib1.dll",
)


def is_excluded_module(name):
    for prefix in EXCLUDED_MODULES:
        if name == prefix or name.startswith(prefix + "."):
            return True
    return False


def is_excluded_package(top):
    return top in EXCLUDED_PACKAGES


def _config_path():
    env = os.environ.get("CRATIX_CONFIG")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    p = Path.cwd() / "cratix.toml"
    return p if p.is_file() else None


def load_policy():
    """读取 cratix.toml 的 [payload] 段；没有配置文件时返回空表。"""
    path = _config_path()
    if path is None:
        return {}
    try:
        import tomllib
    except ImportError:
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 配置坏了不应让 import 崩
        return {}
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else {}


def apply_policy(policy=None):
    """把配置写进模块级表（构建期显式调用）。返回生效后的表快照。"""
    global EXCLUDED_MODULES, EXCLUDED_STDLIB_MODULES, EXCLUDED_STDLIB_EXTENSIONS
    global EXCLUDED_PACKAGES, QT_KEEP_PLUGINS, NATIVE_IN_PACKAGE, PROJECT_TOPS
    policy = load_policy() if policy is None else policy
    if policy.get("exclude_modules"):
        EXCLUDED_MODULES = tuple(policy["exclude_modules"])
    if policy.get("exclude_stdlib"):
        EXCLUDED_STDLIB_MODULES = tuple(policy["exclude_stdlib"])
    if policy.get("exclude_stdlib_extensions"):
        EXCLUDED_STDLIB_EXTENSIONS = tuple(policy["exclude_stdlib_extensions"])
    if policy.get("exclude_packages"):
        EXCLUDED_PACKAGES = tuple(policy["exclude_packages"])
    if policy.get("qt_keep_plugins"):
        QT_KEEP_PLUGINS = tuple(policy["qt_keep_plugins"])
    if policy.get("native_in_package"):
        NATIVE_IN_PACKAGE = tuple(policy["native_in_package"])
    if policy.get("project_tops"):
        PROJECT_TOPS = frozenset(policy["project_tops"])
    return {
        "exclude_modules": EXCLUDED_MODULES,
        "exclude_stdlib": EXCLUDED_STDLIB_MODULES,
        "exclude_stdlib_extensions": EXCLUDED_STDLIB_EXTENSIONS,
        "exclude_packages": EXCLUDED_PACKAGES,
        "qt_keep_plugins": QT_KEEP_PLUGINS,
        "native_in_package": NATIVE_IN_PACKAGE,
        "project_tops": sorted(PROJECT_TOPS),
    }


# 导入即生效（构建期与运行期读同一份配置）
apply_policy()