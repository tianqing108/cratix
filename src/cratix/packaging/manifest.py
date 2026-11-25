"""构建清单 + PUBLIC 边界检查。

边界检查替代原 Nuitka 的 build_report.xml 检查：
    - 领域模型（common.strategy.domain.*）必须在包内；
    - 执行/存储内部模块（common.storage / common.services.executor）不得入包。
"""

from __future__ import annotations

from dataclasses import dataclass, field

REQUIRED_MODULES: tuple[str, ...] = (
    "common.strategy.domain.combination",
    "common.strategy.domain.addition",
    "common.strategy.domain.linkage",
    "common.strategy.domain.strategy",
    "common.strategy.domain.combination_balance",
)

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "common.storage",
    "common.services.executor",
)


@dataclass
class BoundaryResult:
    missing: list[str] = field(default_factory=list)
    leaked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.leaked


def check_public_boundary(modules) -> BoundaryResult:
    names = set(modules)
    result = BoundaryResult()
    result.missing = sorted(m for m in REQUIRED_MODULES if m not in names)
    result.leaked = sorted(m for m in names if m.startswith(FORBIDDEN_PREFIXES))
    return result


def format_boundary(result: BoundaryResult) -> str:
    if result.ok:
        return "PUBLIC 边界检查通过"
    parts = []
    if result.missing:
        parts.append(f"缺失公共模块={result.missing}")
    if result.leaked:
        parts.append(f"泄漏内部模块={result.leaked}")
    return "PUBLIC 边界检查失败: " + " ".join(parts)
