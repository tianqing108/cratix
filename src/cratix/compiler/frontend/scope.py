"""作用域与自由变量分析 ✅（前端第一站 ✅）。

输出（每个函数）：
    locals_    该函数内被绑定的名字 ✅（参数也算 ✅）
    globals_   声明为 global 的名字 ✅
    nonlocals_ 声明为 nonlocal 的名字 ✅
    free       引用但既非局部也非全局的名字 ✅（闭包捕获 ✅）

v1 子集**不支持闭包** ✅ → free 非空即判定该函数不可 AOT ✅（回退 ✅）。
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field

BUILTIN_NAMES = frozenset(dir(builtins))


@dataclass
class Scope:
    name: str
    locals_: set = field(default_factory=set)
    globals_: set = field(default_factory=set)
    nonlocals_: set = field(default_factory=set)
    free: set = field(default_factory=set)
    params: set = field(default_factory=set)

    @property
    def has_closure(self) -> bool:
        return bool(self.free)


class _ScopeAnalyzer(ast.NodeVisitor):
    """收集一个函数体内出现的名字 ✅（区分绑定 / 引用 ✅）。"""

    def __init__(self):
        self.bound: set = set()
        self.reads: set = set()
        self.globals: set = set()
        self.nonlocals: set = set()

    def visit_FunctionDef(self, node):
        self.bound.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Lambda(self, node):
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.bound.add(node.id)
        elif isinstance(node.ctx, ast.Del):
            self.bound.add(node.id)
        else:
            self.reads.add(node.id)

    def visit_Global(self, node):
        self.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.bound.add(alias.asname or alias.name)


def _param_names(args: ast.arguments) -> list:
    names = []
    for arg in list(getattr(args, "posonlyargs", [])) + list(args.args) + list(args.kwonlyargs):
        names.append(arg.arg)
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def analyze_function(node: ast.FunctionDef, module_globals: set | None = None) -> Scope:
    """分析一个函数的局部 / 全局 / 自由变量 ✅。

    module_globals：**模块级绑定的名字** ✅（import 的别名 ✅ / 常量 ✅ / 子函数 ✅）。
    这些名字在函数里是**模块全局** ✅ —— **不是闭包自由变量** ✅。
    （不区分的话 ✅ → `import sys` 后的 sys 会被当成闭包 ✅ → 模块被误拒 ✅）
    """
    analyzer = _ScopeAnalyzer()
    for stmt in node.body:
        analyzer.visit(stmt)
    params = set(_param_names(node.args))
    scope = Scope(name=node.name, params=params)
    scope.globals_ = set(analyzer.globals)
    scope.nonlocals_ = set(analyzer.nonlocals)
    scope.locals_ = (analyzer.bound | params) - scope.globals_ - scope.nonlocals_
    scope.free = (
        analyzer.reads
        - scope.locals_
        - scope.globals_
        - set(module_globals or ())
        - BUILTIN_NAMES
    )
    return scope