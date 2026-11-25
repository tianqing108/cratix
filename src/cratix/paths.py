"""cratix 的路径常量。

注意区分两类根：
    * **工具根**（TOOL_ROOT）：cratix 自身的仓库根 —— stub 源码、运行期引导源码都在这；
    * **项目根**（project root）：被打包的用户项目根 —— 源码、build 目录、产物都在这。
"""

from __future__ import annotations

from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = Path(__file__).resolve().parent
STUB_DIR = PACKAGE_DIR / "stub"
RUNTIME_DIR = PACKAGE_DIR / "runtime"

BUILD_SUBDIR = "build/cratix"
PUBLIC_SUBDIR = "build/public"
LOCK_NAME = ".build.lock"


def build_dir(project_root: Path) -> Path:
    return project_root / "build" / "cratix"


def out_dir(project_root: Path) -> Path:
    return project_root / "build" / "public"