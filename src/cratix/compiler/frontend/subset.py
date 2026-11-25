"""子集判定 ✅：这个函数能不能 AOT 化 ✅。

判定粒度是**函数级** ✅ —— 不支持的部分回退到解释执行 ✅（内嵌 code object ✅）。

不支持的构造（→ 回退 ✅）清单见 UNSUPPORTED 注释 ✅。
"""

from __future__ import annotations

import ast


class Unsupported(Exception):
    """该函数不能 AOT 化 ✅（带原因 ✅ —— 写进 report.json ✅）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# 明确不支持的构造 ✅（原因会逐条进 report ✅）
UNSUPPORTED_NODES = {
    ast.Yield: "生成器（yield）",
    ast.YieldFrom: "生成器（yield from）",
    ast.AsyncFunctionDef: "async 函数",
    ast.AsyncFor: "async for",
    ast.AsyncWith: "async with",
    ast.Await: "await",
    # 类定义单独校验 ✅（见 check_class ✅）

    ast.Match: "match 语句",
    ast.Nonlocal: "nonlocal 声明",
    ast.Delete: "del 语句",
    # **`from ... import ...`** ✅ 已支持 ✅（模块级/函数级同一套生成 ✅）
}


def check_class(node: ast.ClassDef) -> None:
    """检查类定义 ✅（v1 只支持简单的 object 子类 ✅）。

    **不支持** → 抛 Unsupported ✅（整个模块回退 .pyc ✅ —— 类是模块级结构 ✅）。
    """
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "object":
            continue
        raise Unsupported("继承非 object 基类（" + type(base).__name__ + "）")
    if node.keywords:
        raise Unsupported("类定义带关键字参数（metaclass 等）")
    # **类装饰器** ✅：建类之后在运行期按逆序应用 ✅（见 backend/codegen.py ✅）
    for stmt in node.body:
        if isinstance(stmt, ast.FunctionDef):
            continue
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if not isinstance(target, ast.Name):
                    raise Unsupported("类属性赋值目标不是简单名字")
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # 文档字符串 ✅
        if isinstance(stmt, ast.Pass):
            continue
        raise Unsupported("类体语句：" + type(stmt).__name__)


def check_function(node: ast.FunctionDef, scope) -> None:
    """检查函数体 ✅；不支持则抛 Unsupported ✅。

    scope 来自 frontend/scope.py 的 analyze_function ✅。
    """
    if scope.has_closure:
        raise Unsupported("闭包捕获（自由变量：" + ", ".join(sorted(scope.free)[:6]) + "）")
    if scope.nonlocals_:
        raise Unsupported("nonlocal 声明")
    for child in ast.walk(node):
        reason = UNSUPPORTED_NODES.get(type(child))
        if reason:
            raise Unsupported(reason)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del):
            raise Unsupported("del 语句")
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child is not node:
            raise Unsupported("嵌套定义（" + type(child).__name__ + "）")
        if isinstance(child, ast.Lambda):
            raise Unsupported("lambda 表达式（M3 起支持）")
        # **`global` 声明** ✅ 已支持 ✅ —— 作用域分析会把这些名字从局部里剔除 ✅，
        # 赋值走模块字典 ✅（codegen 的 emit_assign 里已有对应分支 ✅）。