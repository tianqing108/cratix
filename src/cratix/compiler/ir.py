"""cratix 自有 IR。

**不用 ast 当 IR** ✅ —— ast 是 Python 的语法树 ✅，我们要的是"可直接生成 C"的语义树 ✅
（常量已折叠 ✅ / 名字已解析 ✅ / 作用域已标注 ✅）。

节点表示：统一的 Node ✅ —— kind（节点类型）+ data（该类型的字段 ✅）+ kids（位置型子节点 ✅）。
复合语句的子语句列表放在 data 里 ✅（body / orelse / handlers ... ✅），
位置型子节点只放"必需且有序"的表达式 ✅（kids ✅）。

字段约定见 KIND_FIELDS ✅。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 每种节点类型的 data 字段 ✅（"kind": (字段名, ...) ✅）
KIND_FIELDS = {
    # --- 模块 / 语句 ---
    "module": ("body",),
    "import": ("names",),                      # names: [(module, asname|None)]
    "importfrom": ("module", "level", "names"),  # from module import names ✅（module 可为空 ✅）                     # names: [(module, asname|None)]
    "import_from": ("module", "names", "level"),
    "assign": ("targets",),                  # kids: [value]
    "augassign": ("op", "target"),            # kids: [value]
    "expr_stmt": (),                         # kids: [value]
    "return": (),                            # kids: [value] | []
    "if": ("body", "orelse"),                # kids: [test]
    "for": ("body", "orelse"),               # kids: [target, iter]
    "while": ("body", "orelse"),             # kids: [test]
    "break": (),
    "continue": (),
    "pass": (),
    "noop": (),                              # 声明类语句 ✅（global/nonlocal ✅）
    "raise": (),                             # kids: [exc|None, cause|None]
    "assert": (),                            # kids: [test, msg|None]
    "try": ("body", "handlers", "orelse", "finalbody"),
    "handler": ("name", "type"),              # kids: [body...]
    "with": ("body",),                       # kids: [ctx, vars|None] 成对 ✅
    "funcdef": ("name", "params", "flags", "body", "key"),
    "classdef": ("name", "bases", "body"),
    # --- 参数 ---
    "params": ("names", "defaults", "kwdefaults", "kwonly", "vararg", "kwarg"),
    # --- 表达式 ---
    "const": ("value",),
    "name": ("id", "ctx"),                   # ctx: load | store | del
    "attr": ("attr", "ctx"),                 # kids: [value]
    "subscript": ("ctx",),                   # kids: [value, slice]
    "binop": ("op",),                        # kids: [left, right]
    "boolop": ("op",),                       # kids: [values...]
    "unaryop": ("op",),                      # kids: [operand]
    "compare": ("ops",),                     # kids: [left, comparators...]
    "call": ("keywords",),                   # keywords: [(arg|None, value)]
    "ifexp": (),                             # kids: [test, body, orelse]
    "list": (),                              # kids: [elts...]
    "tuple": (),                             # kids: [elts...]
    "dict": (),                              # kids: [key|None, value] 成对 ✅
    "set": (),                               # kids: [elts...]
    "listcomp": (),                          # kids: [elt, (target, iter, ifs)...]
    "dictcomp": (),                          # kids: [key, value, (target, iter, ifs)...]
    "setcomp": (),                           # kids: [elt, (target, iter, ifs)...]
    "genexp": ("text", "key"),               # kids: [elt, (target, iter, ifs)...] ✅ text/key ✅
    "lambda": ("params", "key"),             # kids: [body] ✅ params/key 是编译期信息 ✅
    "joinedstr": (),                         # kids: [values...]
    "formattedvalue": ("conversion", "format_spec"),   # kids: [value]
    "starred": (),                           # kids: [value]
    "slice": (),                             # kids: [lower|None, upper|None, step|None]
}


@dataclass
class Node:
    """IR 节点 ✅。"""

    kind: str
    data: dict = field(default_factory=dict)
    kids: list = field(default_factory=list)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __repr__(self) -> str:
        return "Node(" + self.kind + (", " + repr(self.data) if self.data else "") + ")"


def node(kind: str, *kids, **data) -> Node:
    """构造 IR 节点 ✅（kids 位置参数 ✅ / data 关键字参数 ✅）。"""
    if kind not in KIND_FIELDS:
        raise KeyError("未登记的 IR 节点类型：" + kind)
    allowed = KIND_FIELDS[kind]
    for key in data:
        if key not in allowed:
            raise KeyError("IR 节点 " + kind + " 不接受字段 " + key + "（允许：" + str(allowed) + "）")
    return Node(kind, dict(data), list(kids))


def walk(n: Node):
    """深度优先遍历 ✅（含自身 ✅）。"""
    stack = [n]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(cur.kids))