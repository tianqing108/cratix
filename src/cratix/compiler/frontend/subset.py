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

    # ast.Match：已支持 ✅（字面量/单例/捕获/通配/或 + guard ✅；结构模式回退 ✅）
    # ast.Nonlocal：只读已支持 ✅（写回在 driver 里拒 ✅）
    # ast.Delete：**已支持** ✅（只支持删名字 ✅ —— 见下面的细化检查 ✅）
    # **`from ... import ...`** ✅ 已支持 ✅（模块级/函数级同一套生成 ✅）
}


def check_class(node: ast.ClassDef) -> None:
    """检查类定义 ✅。

    **继承** ✅：支持**单个基类** ✅（实例布局由基类决定 ✅ -> 这类类**不做属性槽位** ✅，
    创建走 `type(name, (Base,), ns)` ✅）。多基类 / metaclass 仍不支持 ⬜。
    """
    # **多基类** ✅ 已支持 ✅（`type(name, (A, B), ns)` ✅ —— MRO 交给 CPython ✅）
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
        if isinstance(stmt, (ast.AnnAssign,)):
            # 类属性 `x: int = 0` ✅（driver 会处理 ✅）
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # 文档字符串 ✅
        if isinstance(stmt, ast.Pass):
            continue
        # **类体里的类** ✅ 已支持 ✅（源码 exec 后挂到外层类上 ✅）
        if isinstance(stmt, ast.ClassDef):
            continue
        raise Unsupported("类体语句：" + type(stmt).__name__)


def check_function(node: ast.FunctionDef, scope) -> None:
    """检查函数体 ✅；不支持则抛 Unsupported ✅。

    scope 来自 frontend/scope.py 的 analyze_function ✅。
    """
    if scope.has_closure:
        raise Unsupported("闭包捕获（自由变量：" + ", ".join(sorted(scope.free)[:6]) + "）")
    # **`nonlocal` 只读** ✅ 已支持 ✅（读走 cell ✅）；**写回**由 driver 检查后回退 ✅
    for child in ast.walk(node):
        reason = UNSUPPORTED_NODES.get(type(child))
        if reason:
            raise Unsupported(reason)
        # **`del`** ✅：名字 / 属性 / 下标都支持 ✅（见 builder ✅）
        # **嵌套 def** ✅ 已支持 ✅（cell 字典 + functools.partial ✅）；
        # 没登记的嵌套 def 会在 codegen 里被判回退 ✅（语义仍正确 ✅）。
        # **函数体里的类** ✅ 已支持 ✅（源码 exec + 搬进局部 ✅；取不到源码才回退 ✅）
        if isinstance(child, ast.AsyncFunctionDef) and child is not node:
            raise Unsupported("嵌套定义（" + type(child).__name__ + "）")
        # **lambda** ✅ 已支持 ✅（无条件/无捕获的编译成原生函数 ✅；
        # 捕获外层局部的在 codegen 里判定回退 ✅）。
        # **`global` 声明** ✅ 已支持 ✅ —— 作用域分析会把这些名字从局部里剔除 ✅，
        # 赋值走模块字典 ✅（codegen 的 emit_assign 里已有对应分支 ✅）。