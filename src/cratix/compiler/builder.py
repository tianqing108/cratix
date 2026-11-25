"""ast -> IR 构建器 ✅。

只处理 v1 子集 ✅；遇到不支持的构造抛 Unsupported ✅。
**常量折叠** ✅ 在这里做 ✅（只折叠字面量 ✅ —— 不改变语义 ✅）。
"""

from __future__ import annotations

import ast

from . import ir
from .frontend.subset import Unsupported

BIN_OPS = {
    "Add": "+", "Sub": "-", "Mult": "*", "Div": "/", "FloorDiv": "//", "Mod": "%",
    "Pow": "**", "LShift": "<<", "RShift": ">>", "BitOr": "|", "BitXor": "^", "BitAnd": "&",
}
UNARY_OPS = {"UAdd": "+", "USub": "-", "Not": "not", "Invert": "~"}
CMP_OPS = {
    "Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">", "GtE": ">=",
    "Is": "is", "IsNot": "is not", "In": "in", "NotIn": "not in",
}


def build_module(tree: ast.Module) -> ir.Node:
    return ir.node("module", body=[build_stmt(s) for s in tree.body])


def build_params(args: ast.arguments) -> ir.Node:
    """参数表 ✅（位置 / 默认值 / 仅关键字 / *args / **kwargs ✅）。"""
    positional = [a.arg for a in list(getattr(args, "posonlyargs", [])) + list(args.args)]
    defaults = list(args.defaults)
    kwonly = [a.arg for a in args.kwonlyargs]
    kwdefaults = list(args.kw_defaults)
    pad = len(positional) - len(defaults)
    def_list = [None] * pad + [build_expr(d) for d in defaults]
    kw_def = [None if d is None else build_expr(d) for d in kwdefaults]
    vararg = args.vararg.arg if args.vararg else None
    kwarg = args.kwarg.arg if args.kwarg else None
    return ir.node(
        "params",
        names=positional,
        defaults=def_list,
        kwdefaults=kw_def,
        kwonly=kwonly,
        vararg=vararg,
        kwarg=kwarg,
    )


def build_stmt(node: ast.stmt) -> ir.Node:
    """语句 -> IR ✅。"""
    if isinstance(node, ast.Expr):
        return ir.node("expr_stmt", build_expr(node.value))
    if isinstance(node, ast.Assign):
        return ir.node(
            "assign",
            build_expr(node.value),
            targets=[build_expr(t) for t in node.targets],
        )
    if isinstance(node, ast.AnnAssign):
        if node.value is None:
            return ir.node("pass")
        return ir.node("assign", build_expr(node.value), targets=[build_expr(node.target)])
    if isinstance(node, ast.AugAssign):
        op = BIN_OPS.get(type(node.op).__name__)
        if op is None:
            raise Unsupported("复合赋值运算符：" + type(node.op).__name__)
        return ir.node("augassign", build_expr(node.value), op=op, target=build_expr(node.target))
    if isinstance(node, ast.Return):
        if node.value is None:
            return ir.node("return")
        return ir.node("return", build_expr(node.value))
    if isinstance(node, ast.If):
        return ir.node(
            "if",
            build_expr(node.test),
            body=[build_stmt(s) for s in node.body],
            orelse=[build_stmt(s) for s in node.orelse],
        )
    if isinstance(node, ast.For):
        return ir.node(
            "for",
            build_expr(node.target),
            build_expr(node.iter),
            body=[build_stmt(s) for s in node.body],
            orelse=[build_stmt(s) for s in node.orelse],
        )
    if isinstance(node, ast.While):
        return ir.node(
            "while",
            build_expr(node.test),
            body=[build_stmt(s) for s in node.body],
            orelse=[build_stmt(s) for s in node.orelse],
        )
    if isinstance(node, ast.Break):
        return ir.node("break")
    if isinstance(node, ast.Continue):
        return ir.node("continue")
    if isinstance(node, ast.Pass):
        return ir.node("pass")
    if isinstance(node, ast.Raise):
        if node.exc is None:
            return ir.node("raise")
        cause = build_expr(node.cause) if node.cause is not None else None
        return ir.node("raise", build_expr(node.exc), cause)
    if isinstance(node, ast.Assert):
        msg = build_expr(node.msg) if node.msg is not None else None
        return ir.node("assert", build_expr(node.test), msg)
    if isinstance(node, ast.Try):
        handlers = []
        for h in node.handlers:
            htype = build_expr(h.type) if h.type is not None else None
            handlers.append(ir.node("handler", *[build_stmt(s) for s in h.body], name=h.name, type=htype))
        return ir.node(
            "try",
            body=[build_stmt(s) for s in node.body],
            handlers=handlers,
            orelse=[build_stmt(s) for s in node.orelse],
            finalbody=[build_stmt(s) for s in node.finalbody],
        )
    if isinstance(node, ast.With):
        pairs = []
        for item in node.items:
            ctx = build_expr(item.context_expr)
            var = build_expr(item.optional_vars) if item.optional_vars is not None else None
            pairs.extend([ctx, var])
        return ir.node("with", *pairs, body=[build_stmt(s) for s in node.body])
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        # **`global` / `nonlocal` 声明** ✅ —— 运行期**没有动作** ✅：
        # 作用域分析已经把这些名字从"局部"里剔除 ✅ -> 赋值自然走模块字典 ✅。
        return ir.node("noop")
    if isinstance(node, ast.Import):
        names = []
        for alias in node.names:
            names.append((alias.name, alias.asname))
        return ir.node("import", names=names)
    if isinstance(node, ast.ImportFrom):
        # **`from ... import ...`** ✅ —— 真实代码里到处都是 ✅（不支持的话整个模块都得回退 ✅）。
        # 相对导入的**绝对名**由 codegen 按模块包名解析 ✅（builder 不知道包名 ✅）。
        names = []
        for alias in node.names:
            if alias.name == "*":
                raise Unsupported("from ... import *（整函数回退）")
            names.append((alias.name, alias.asname))
        return ir.node("importfrom", module=node.module or "", level=node.level, names=names)
    if isinstance(node, ast.FunctionDef):
        # **嵌套 def** ✅：在函数体里出现时，codegen 会按 (行, 列) 查登记表 ✅
        # （模块级/类方法由 driver 处理 ✅，不走这里 ✅）。
        flags = ("async", bool(node.decorator_list))
        return ir.node(
            "funcdef",
            key=(getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
            name=node.name,
            params=build_params(node.args),
            flags=flags,
            body=[build_stmt(s) for s in node.body],
        )
    if isinstance(node, ast.ClassDef):
        return ir.node(
            "classdef",
            name=node.name,
            bases=[build_expr(b) for b in node.bases],
            body=[build_stmt(s) for s in node.body],
        )
    raise Unsupported("语句类型：" + type(node).__name__)

def _ctx(node: ast.expr) -> str:
    if isinstance(node.ctx, ast.Store):
        return "store"
    if isinstance(node.ctx, ast.Del):
        return "del"
    return "load"


def _const(value):
    """常量节点 ✅（None / bool / int / float / str / bytes ✅）。"""
    return ir.node("const", value=value)


def _fold(op: str, left, right):
    """常量折叠 ✅（只折叠**安全**的字面量运算 ✅ —— 不改变语义 ✅）。"""
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    try:
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right
        if op == "//":
            return left // right
        if op == "%":
            return left % right
        if op == "**":
            return left**right
    except Exception:  # noqa: BLE001 - 折叠失败就放弃折叠 ✅（语义交给运行期 ✅）
        return None
    return None


def build_function(node: ast.FunctionDef) -> dict:
    """把一个 FunctionDef 建成「参数 + 语句表」✅（嵌套 def 复用 ✅）。"""
    return {
        "params": build_params(node.args),
        "body": [build_stmt(stmt) for stmt in node.body],
    }


def build_lambda(node: ast.Lambda) -> dict:
    """把 lambda 建成一个**普通函数**的形态 ✅（参数 + `return 表达式` ✅）。"""
    return {
        "params": build_params(node.args),
        "body": [ir.node("return", build_expr(node.body))],
    }


def build_expr(node: ast.expr) -> ir.Node:
    """表达式 -> IR ✅。"""
    if isinstance(node, ast.Constant):
        return _const(node.value)
    if isinstance(node, ast.Name):
        return ir.node("name", id=node.id, ctx=_ctx(node))
    if isinstance(node, ast.Attribute):
        return ir.node("attr", build_expr(node.value), attr=node.attr, ctx=_ctx(node))
    if isinstance(node, ast.Subscript):
        return ir.node("subscript", build_expr(node.value), build_expr(node.slice), ctx=_ctx(node))
    if isinstance(node, ast.Slice):
        lower = build_expr(node.lower) if node.lower is not None else None
        upper = build_expr(node.upper) if node.upper is not None else None
        step = build_expr(node.step) if node.step is not None else None
        return ir.node("slice", lower, upper, step)
    if isinstance(node, ast.BinOp):
        op = BIN_OPS.get(type(node.op).__name__)
        if op is None:
            raise Unsupported("二元运算符：" + type(node.op).__name__)
        left = build_expr(node.left)
        right = build_expr(node.right)
        if left.kind == "const" and right.kind == "const":
            folded = _fold(op, left.get("value"), right.get("value"))
            if folded is not None:
                return _const(folded)
        return ir.node("binop", left, right, op=op)
    if isinstance(node, ast.BoolOp):
        op = "and" if isinstance(node.op, ast.And) else "or"
        return ir.node("boolop", *[build_expr(v) for v in node.values], op=op)
    if isinstance(node, ast.UnaryOp):
        op = UNARY_OPS.get(type(node.op).__name__)
        if op is None:
            raise Unsupported("一元运算符：" + type(node.op).__name__)
        return ir.node("unaryop", build_expr(node.operand), op=op)
    if isinstance(node, ast.Compare):
        ops = []
        for o in node.ops:
            sym = CMP_OPS.get(type(o).__name__)
            if sym is None:
                raise Unsupported("比较运算符：" + type(o).__name__)
            ops.append(sym)
        return ir.node(
            "compare",
            build_expr(node.left),
            *[build_expr(c) for c in node.comparators],
            ops=ops,
        )
    if isinstance(node, ast.Call):
        args = []
        for a in node.args:
            if isinstance(a, ast.Starred):
                args.append(ir.node("starred", build_expr(a.value)))
            else:
                args.append(build_expr(a))
        keywords = []
        for kw in node.keywords:
            if kw.arg is None:
                keywords.append((None, ir.node("starred", build_expr(kw.value))))
            else:
                keywords.append((kw.arg, build_expr(kw.value)))
        return ir.node("call", build_expr(node.func), *args, keywords=keywords)
    if isinstance(node, ast.IfExp):
        return ir.node("ifexp", build_expr(node.test), build_expr(node.body), build_expr(node.orelse))
    if isinstance(node, ast.List):
        return ir.node("list", *[build_expr(e) for e in node.elts])
    if isinstance(node, ast.Tuple):
        return ir.node("tuple", *[build_expr(e) for e in node.elts])
    if isinstance(node, ast.Set):
        return ir.node("set", *[build_expr(e) for e in node.elts])
    if isinstance(node, ast.Dict):
        kids = []
        for k, v in zip(node.keys, node.values):
            kids.append(build_expr(k) if k is not None else None)
            kids.append(build_expr(v))
        return ir.node("dict", *kids)
    if isinstance(node, ast.GeneratorExp):
        # **生成器表达式** ✅：真的要"惰性" ✅ -> 必须产出一个**真生成器对象** ✅。
        # 做法 ✅：把生成器函数**源码**带进 IR ✅（`ast.unparse` ✅ 不依赖源文件 ✅），
        # 由 C 后端在模块 init 里建出这个函数 ✅，调用点只负责传**最外层可迭代对象** ✅。
        gens = []
        texts = []
        for g in node.generators:
            if g.is_async:
                raise Unsupported("异步生成器表达式")
            gens.append((
                build_expr(g.target),
                build_expr(g.iter),
                [build_expr(c) for c in g.ifs],
            ))
            texts.append((
                ast.unparse(g.target),
                ast.unparse(g.iter),
                [ast.unparse(c) for c in g.ifs],
            ))
        return ir.node(
            "genexp",
            build_expr(node.elt),
            *gens,
            text={"elt": ast.unparse(node.elt), "gens": texts},
        )
    if isinstance(node, (ast.ListComp, ast.SetComp)):
        gens = []
        for g in node.generators:
            if g.is_async:
                raise Unsupported("异步推导式")
            gens.append((
                build_expr(g.target),
                build_expr(g.iter),
                [build_expr(c) for c in g.ifs],
            ))
        kind = "listcomp" if isinstance(node, ast.ListComp) else "setcomp"
        return ir.node(kind, build_expr(node.elt), *gens)
    if isinstance(node, ast.DictComp):
        gens = []
        for g in node.generators:
            if g.is_async:
                raise Unsupported("异步推导式")
            gens.append((
                build_expr(g.target),
                build_expr(g.iter),
                [build_expr(c) for c in g.ifs],
            ))
        return ir.node("dictcomp", build_expr(node.key), build_expr(node.value), *gens)
    if isinstance(node, ast.JoinedStr):
        return ir.node("joinedstr", *[build_expr(v) for v in node.values])
    if isinstance(node, ast.FormattedValue):
        conv = node.conversion if node.conversion is not None else -1
        spec = build_expr(node.format_spec) if node.format_spec is not None else None
        return ir.node("formattedvalue", build_expr(node.value), conversion=conv, format_spec=spec)
    if isinstance(node, ast.Starred):
        return ir.node("starred", build_expr(node.value))
    if isinstance(node, ast.Lambda):
        # **lambda** ✅：车成模块级的**原生函数** ✅（与普通函数同一套生成 ✅），
        # 使用点只负责取到那个函数对象 ✅（见 codegen 的 "lambda" 分支 ✅）。
        # 用 (行, 列) 当稳定键 ✅ —— builder 与 driver 各自都能从 AST 算出来 ✅。
        key = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
        return ir.node(
            "lambda",
            build_expr(node.body),
            params=build_params(node.args),
            key=key,
        )
    if isinstance(node, ast.NamedExpr):
        raise Unsupported("海象运算符 :=")
    if isinstance(node, ast.Await):
        raise Unsupported("await")
    if isinstance(node, ast.Yield):
        raise Unsupported("yield")
    raise Unsupported("表达式类型：" + type(node).__name__)