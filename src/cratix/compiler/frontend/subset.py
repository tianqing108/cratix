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
    ast.ClassDef: "类定义（M3 起支持）",
    ast.Match: "match 语句",
    ast.Nonlocal: "nonlocal 声明",
    ast.Delete: "del 语句",
    ast.ImportFrom: "from ... import（模块级导入单独处理 ✅）",
}


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
        if isinstance(child, ast.Global):
            raise Unsupported("global 声明（写全局变量）")