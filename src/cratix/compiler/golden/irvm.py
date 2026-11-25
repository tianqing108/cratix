"""IR 解释执行器（黄金模型 ✅）。

目的：**在写 C 后端之前** ✅ 先用 IR 解释执行验证前端语义 ✅ ——
把"语义 bug"与"C 生成 bug"分开定位 ✅（C 生成的 bug 难查得多 ✅）。

语义对齐 CPython ✅：
    - 名字查找顺序：局部 -> 模块全局 -> 内建 ✅；
    - 布尔运算短路 ✅；比较链 ✅；
    - 异常匹配用 isinstance ✅；
    - with 走 __enter__/__exit__ ✅；
    - 推导式有独立作用域 ✅（Python 3 ✅）。
"""

from __future__ import annotations

import builtins
import operator

BIN_FUNCS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
    "<<": operator.lshift,
    ">>": operator.rshift,
    "|": operator.or_,
    "^": operator.xor,
    "&": operator.and_,
}

CMP_FUNCS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "is": operator.is_,
    "is not": operator.is_not,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, value):
        super().__init__("return")
        self.value = value


class Function:
    """IR 函数对象 ✅（可调用 ✅ + 描述符 ✅ —— 方法绑定 self ✅）。"""

    def __init__(self, name, params, body, globals_dict):
        self.name = name
        self.params = params
        self.body = body
        self.globals = globals_dict

    def __repr__(self):
        return "<irvm.Function " + self.name + ">"

    def __call__(self, *args, **kwargs):
        return Interpreter(self.globals).call_function(self, args, kwargs)

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return _BoundMethod(self, obj)


class _BoundMethod:
    def __init__(self, func, obj):
        self.func = func
        self.obj = obj

    def __call__(self, *args, **kwargs):
        return self.func(self.obj, *args, **kwargs)


class Interpreter:
    """IR 解释器 ✅。"""

    def __init__(self, globals_dict=None):
        self.globals = globals_dict if globals_dict is not None else {}

    def _lookup(self, name, frame):
        if name in frame.locals:
            return frame.locals[name]
        if name in self.globals:
            return self.globals[name]
        if hasattr(builtins, name):
            return getattr(builtins, name)
        raise NameError("name " + repr(name) + " is not defined")

    def _assign(self, name, value, frame):
        if name in frame.locals:
            frame.locals[name] = value
        else:
            self.globals[name] = value

    def run_module(self, module_ir):
        frame = _Frame(self.globals)
        for stmt in module_ir.get("body", []):
            self.exec_stmt(stmt, frame)
        return self.globals

    def call_function(self, func, args, kwargs=None):
        kwargs = dict(kwargs or {})
        params = func.params
        names = list(params.get("names", []))
        defaults = list(params.get("defaults", []))
        kwonly = list(params.get("kwonly", []))
        vararg = params.get("vararg")
        kwarg = params.get("kwarg")
        local = {}
        positional = list(args)
        if len(positional) > len(names):
            if vararg is None:
                raise TypeError("too many positional arguments")
            local[vararg] = tuple(positional[len(names):])
            positional = positional[: len(names)]
        for i, value in enumerate(positional):
            local[names[i]] = value
        for i in range(len(positional), len(names)):
            default = defaults[i] if i < len(defaults) else None
            if default is None:
                raise TypeError("missing required argument: " + names[i])
            local[names[i]] = self.eval(default, _Frame(self.globals))
        kw_defaults = params.get("kwdefaults") or []
        for i, name in enumerate(kwonly):
            if name in kwargs:
                local[name] = kwargs.pop(name)
            elif i < len(kw_defaults) and kw_defaults[i] is not None:
                local[name] = self.eval(kw_defaults[i], _Frame(self.globals))
            else:
                raise TypeError("missing required keyword-only argument: " + name)
        if kwargs:
            if kwarg is None:
                raise TypeError("unexpected keyword argument: " + next(iter(kwargs)))
            local[kwarg] = kwargs
        # *args / **kwargs 总是存在 ✅（没传时为空 ✅ —— 否则函数体里引用会 NameError ✅）
        if vararg is not None and vararg not in local:
            local[vararg] = ()
        if kwarg is not None and kwarg not in local:
            local[kwarg] = {}
        frame = _Frame(self.globals, local)
        try:
            for stmt in func.body:
                self.exec_stmt(stmt, frame)
        except _Return as ret:
            return ret.value
        return None
    def exec_block(self, stmts, frame):
        for stmt in stmts:
            self.exec_stmt(stmt, frame)

    def exec_stmt(self, node, frame):
        kind = node.kind
        if kind == "expr_stmt":
            self.eval(node.kids[0], frame)
            return
        if kind == "assign":
            value = self.eval(node.kids[0], frame)
            for target in node.get("targets", []):
                self.assign_to(target, value, frame)
            return
        if kind == "augassign":
            target = node.get("target")
            current = self.eval(target, frame)
            value = self.eval(node.kids[0], frame)
            self.assign_to(target, BIN_FUNCS[node.get("op")](current, value), frame)
            return
        if kind == "return":
            value = self.eval(node.kids[0], frame) if node.kids else None
            raise _Return(value)
        if kind == "if":
            if truth(self.eval(node.kids[0], frame)):
                self.exec_block(node.get("body", []), frame)
            else:
                self.exec_block(node.get("orelse", []), frame)
            return
        if kind == "while":
            broke = False
            while truth(self.eval(node.kids[0], frame)):
                try:
                    self.exec_block(node.get("body", []), frame)
                except _Break:
                    broke = True
                    break
                except _Continue:
                    continue
            if not broke:
                self.exec_block(node.get("orelse", []), frame)
            return
        if kind == "for":
            iterable = self.eval(node.kids[1], frame)
            broke = False
            for item in iterable:
                self.assign_to(node.kids[0], item, frame)
                try:
                    self.exec_block(node.get("body", []), frame)
                except _Break:
                    broke = True
                    break
                except _Continue:
                    continue
            if not broke:
                self.exec_block(node.get("orelse", []), frame)
            return
        if kind == "break":
            raise _Break()
        if kind == "continue":
            raise _Continue()
        if kind == "pass":
            return
        if kind == "raise":
            if not node.kids:
                raise RuntimeError("No active exception to reraise")
            exc = self.eval(node.kids[0], frame)
            cause = self.eval(node.kids[1], frame) if len(node.kids) > 1 and node.kids[1] else None
            if cause is not None:
                raise exc.__cause__(cause)
            raise exc
        if kind == "assert":
            if not truth(self.eval(node.kids[0], frame)):
                msg = self.eval(node.kids[1], frame) if len(node.kids) > 1 and node.kids[1] else None
                raise AssertionError(msg if msg is not None else "")
            return
        if kind == "try":
            self._exec_try(node, frame)
            return
        if kind == "with":
            self._exec_with(node, frame)
            return
        if kind == "funcdef":
            func = Function(
                node.get("name"),
                node.get("params"),
                node.get("body", []),
                self.globals,
            )
            self._assign(node.get("name"), func, frame)
            return
        if kind == "classdef":
            self._exec_classdef(node, frame)
            return
        if kind == "import":
            for module_name, asname in node.get("names", []):
                imported = __import__(module_name, fromlist=["*"])
                self._assign(asname or module_name.split(".")[0], imported, frame)
            return
        raise NotImplementedError("IR 语句未实现：" + kind)

    def _exec_try(self, node, frame):
        try:
            try:
                self.exec_block(node.get("body", []), frame)
            except Exception as exc:
                for handler in node.get("handlers", []):
                    htype = handler.get("type")
                    if htype is None or isinstance(exc, self.eval(htype, frame)):
                        name = handler.get("name")
                        if name:
                            self._assign(name, exc, frame)
                        self.exec_block(handler.kids, frame)
                        break
                else:
                    raise
            else:
                self.exec_block(node.get("orelse", []), frame)
        finally:
            self.exec_block(node.get("finalbody", []), frame)

    def _exec_with(self, node, frame):
        kids = node.kids
        entered = []
        error = None
        try:
            for i in range(0, len(kids), 2):
                ctx = self.eval(kids[i], frame)
                value = ctx.__enter__()
                entered.append((ctx, value, kids[i + 1]))
                if kids[i + 1] is not None:
                    self.assign_to(kids[i + 1], value, frame)
            self.exec_block(node.get("body", []), frame)
        except (BaseException) as exc:  # noqa: BLE001 - 控制流也用异常表达 ✅
            error = exc
        # **控制流** ✅（return/break/continue ✅）—— 只跑 __exit__(None, None, None) ✅
        control = isinstance(error, (_Return, _Break, _Continue))
        if error is None or control:
            for ctx, _value, _var in reversed(entered):
                ctx.__exit__(None, None, None)
            if error is not None:
                raise error
            return
        # **异常路径** ✅ —— 由内向外 ✅；一旦某层抑制 ✅ 外层按正常退出 ✅（CPython 语义 ✅）
        suppressed = False
        for ctx, _value, _var in reversed(entered):
            if suppressed:
                ctx.__exit__(None, None, None)
            elif ctx.__exit__(type(error), error, error.__traceback__):
                suppressed = True
        if not suppressed:
            raise error

    def _exec_classdef(self, node, frame):
        bases = tuple(self.eval(b, frame) for b in node.get("bases", []))
        namespace = {"__name__": self.globals.get("__name__", "__main__")}
        class_frame = _Frame(self.globals, namespace)
        for stmt in node.get("body", []):
            self.exec_stmt(stmt, class_frame)
        cls = type(node.get("name"), bases, namespace)
        self._assign(node.get("name"), cls, frame)
    def assign_to(self, target, value, frame):
        kind = target.kind
        if kind == "name":
            self._assign(target.get("id"), value, frame)
            return
        if kind == "attr":
            obj = self.eval(target.kids[0], frame)
            setattr(obj, target.get("attr"), value)
            return
        if kind == "subscript":
            obj = self.eval(target.kids[0], frame)
            key = self.eval(target.kids[1], frame)
            obj[key] = value
            return
        if kind in ("tuple", "list"):
            items = list(value)
            targets = list(target.kids)
            star_index = -1
            for i, sub in enumerate(targets):
                if sub.kind == "starred":
                    star_index = i
                    break
            if star_index < 0:
                if len(items) != len(targets):
                    raise ValueError("unpack count mismatch")
                for sub, item in zip(targets, items):
                    self.assign_to(sub, item, frame)
                return
            before = star_index
            after = len(targets) - star_index - 1
            if len(items) < before + after:
                raise ValueError("unpack count mismatch")
            for i in range(before):
                self.assign_to(targets[i], items[i], frame)
            rest = items[before: len(items) - after]
            self.assign_to(targets[star_index].kids[0], rest, frame)
            for j in range(after):
                self.assign_to(targets[star_index + 1 + j], items[len(items) - after + j], frame)
            return
        raise NotImplementedError("赋值目标未实现：" + kind)

    def eval(self, node, frame):
        kind = node.kind
        if kind == "const":
            return node.get("value")
        if kind == "name":
            return self._lookup(node.get("id"), frame)
        if kind == "attr":
            return getattr(self.eval(node.kids[0], frame), node.get("attr"))
        if kind == "subscript":
            return self.eval(node.kids[0], frame)[self.eval(node.kids[1], frame)]
        if kind == "binop":
            return BIN_FUNCS[node.get("op")](
                self.eval(node.kids[0], frame),
                self.eval(node.kids[1], frame),
            )
        if kind == "boolop":
            result = self.eval(node.kids[0], frame)
            if node.get("op") == "and":
                for item in node.kids[1:]:
                    if not truth(result):
                        return result
                    result = self.eval(item, frame)
                return result
            for item in node.kids[1:]:
                if truth(result):
                    return result
                result = self.eval(item, frame)
            return result
        if kind == "unaryop":
            value = self.eval(node.kids[0], frame)
            op = node.get("op")
            if op == "not":
                return not truth(value)
            if op == "-":
                return -value
            if op == "+":
                return +value
            if op == "~":
                return ~value
            raise NotImplementedError("一元运算符：" + str(op))
        if kind == "compare":
            left = self.eval(node.kids[0], frame)
            ops = node.get("ops", [])
            for i, op in enumerate(ops):
                right = self.eval(node.kids[i + 1], frame)
                if not CMP_FUNCS[op](left, right):
                    return False
                left = right
            return True
        if kind == "call":
            func = self.eval(node.kids[0], frame)
            args = []
            kwargs = {}
            for child in node.kids[1:]:
                if child.kind == "starred":
                    args.extend(self.eval(child.kids[0], frame))
                else:
                    args.append(self.eval(child, frame))
            for name, child in node.get("keywords", []):
                value = self.eval(child, frame)
                if name is None:
                    kwargs.update(value)
                else:
                    kwargs[name] = value
            return func(*args, **kwargs)
        if kind == "ifexp":
            if truth(self.eval(node.kids[0], frame)):
                return self.eval(node.kids[1], frame)
            return self.eval(node.kids[2], frame)
        if kind == "list":
            return [self.eval(k, frame) for k in node.kids]
        if kind == "tuple":
            return tuple(self.eval(k, frame) for k in node.kids)
        if kind == "set":
            return {self.eval(k, frame) for k in node.kids}
        if kind == "dict":
            out = {}
            for i in range(0, len(node.kids), 2):
                key = node.kids[i]
                value = node.kids[i + 1]
                if key is None:
                    out.update(self.eval(value, frame))
                else:
                    out[self.eval(key, frame)] = self.eval(value, frame)
            return out
        if kind in ("listcomp", "setcomp", "dictcomp"):
            return self._eval_comprehension(node, frame)
        if kind == "genexp":
            return self._eval_genexp(node, frame)
        if kind == "joinedstr":
            parts = []
            for child in node.kids:
                value = self.eval(child, frame)
                parts.append(value if isinstance(value, str) else str(value))
            return "".join(parts)
        if kind == "formattedvalue":
            value = self.eval(node.kids[0], frame)
            conv = node.get("conversion", -1)
            if conv == 115:
                value = str(value)
            elif conv == 114:
                value = repr(value)
            elif conv == 97:
                value = ascii(value)
            spec = node.get("format_spec")
            if spec is not None:
                return format(value, self.eval(spec, frame))
            return str(value)
        if kind == "slice":
            lower = self.eval(node.kids[0], frame) if node.kids[0] is not None else None
            upper = self.eval(node.kids[1], frame) if node.kids[1] is not None else None
            step = self.eval(node.kids[2], frame) if node.kids[2] is not None else None
            return slice(lower, upper, step)
        if kind == "starred":
            return self.eval(node.kids[0], frame)
        raise NotImplementedError("IR 表达式未实现：" + kind)

    def _eval_genexp(self, node, frame):
        """生成器表达式 ✅ —— **真生成器** ✅（惰性 ✅）。

        做法 ✅：用 IR 里带的源码片段**合成表达式** ✅ 再 `eval` ✅ ——
        最外层可迭代对象已按 Python 语义**立即求值** ✅，塞进临时名字 ✅。
        """
        text = node.get("text") or {}
        elt_src = text.get("elt")
        gen_texts = text.get("gens") or []
        gens = list(node.kids[1:])
        if not elt_src or len(gen_texts) != len(gens):
            raise NotImplementedError("生成器表达式缺少源码片段")
        parts = []
        for index, (target_src, iter_src, ifs) in enumerate(gen_texts):
            iterable = "__cratix_it0" if index == 0 else "(" + iter_src + ")"
            parts.append(" for " + target_src + " in " + iterable)
            for cond in ifs:
                parts.append(" if " + cond)
        source = "(" + elt_src + "".join(parts) + ")"
        first = self.eval(gens[0][1], frame)
        # **外层局部也要可见** ✅（生成器表达式可以捕获外层变量 ✅）——
        # 只给 globals 会让 `x + offset` 里的 offset 报 NameError ✅（实测 ✅）。
        # **eval 里的生成器没有"闭包"** ✅ —— 外层名字会被当成**全局**查 ✅，
        # 所以要把外层局部并进求值作用域 ✅（只放 locals 仍然 NameError ✅ 实测 ✅）。
        scope = dict(self.globals)
        scope.update(frame.locals)
        scope["__cratix_it0"] = first
        return eval(source, scope, scope)  # noqa: S307 - 受控源码 ✅

    def _eval_comprehension(self, node, frame):
        kind = node.kind
        offset = 2 if kind == "dictcomp" else 1
        gens = list(node.kids[offset:])
        results = []
        mapping = {}
        # **独立作用域** ✅（Python 3 ✅）—— 赋值落在推导式自己的帧里 ✅，不污染函数局部 ✅。
        # 外层局部要**可见** ✅ -> 复制一份（读回退到 globals/内建 ✅）。
        comp_frame = _Frame(frame.globals, dict(frame.locals))

        def target_names(target, acc):
            """收集目标名 ✅ —— **必须返回 acc** ✅（调用方要迭代它 ✅）。"""
            if target is None:
                return acc
            if target.kind == "name":
                acc.append(target.get("id"))
                return acc
            if target.kind == "starred":
                return target_names(target.kids[0], acc)
            for kid in target.kids:
                target_names(kid, acc)
            return acc

        # **先把目标名种进 locals** ✅ —— 否则新名字会被写到模块全局 ✅（泄漏 ✅）
        for target, _iterable, _ifs in gens:
            for name in target_names(target, []):
                comp_frame.locals.setdefault(name, None)

        def rec(index):
            if index >= len(gens):
                if kind == "dictcomp":
                    mapping[self.eval(node.kids[0], comp_frame)] = self.eval(node.kids[1], comp_frame)
                else:
                    results.append(self.eval(node.kids[0], comp_frame))
                return
            target, iterable, ifs = gens[index]
            # **最外层可迭代对象在"外层作用域"求值** ✅（`[x for x in x]` ✅）
            iter_frame = frame if index == 0 else comp_frame
            for item in self.eval(iterable, iter_frame):
                self.assign_to(target, item, comp_frame)
                ok = True
                for cond in ifs:
                    if not truth(self.eval(cond, comp_frame)):
                        ok = False
                        break
                if ok:
                    rec(index + 1)

        rec(0)
        if kind == "dictcomp":
            return mapping
        if kind == "setcomp":
            return set(results)
        return results


class _Frame:
    def __init__(self, globals_dict, locals_dict=None):
        self.globals = globals_dict
        self.locals = locals_dict if locals_dict is not None else {}


def truth(value) -> bool:
    """Python 真值语义 ✅。"""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset)):
        return len(value) > 0
    return bool(value)