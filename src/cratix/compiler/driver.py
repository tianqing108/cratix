"""AOT 编译器驱动：源码模块 -> IR -> C -> .pyd ✅。

v1 的判定与生成都做**函数级** ✅：
    - 能 AOT 的函数 -> 生成原生函数 ✅；
    - 不能 AOT 的函数 -> 记下原因 ✅（后续做内嵌 code object 的函数级回退 ✅）。

模块级只支持**常量赋值**与 **import** ✅（其余 -> 整个模块回退 ✅）。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .. import platforms
from . import builder
from .backend import codegen
from .backend.runtime import HEADER_NAME, RUNTIME_H
from .frontend.scope import Scope, analyze_function, module_bindings
from .frontend.subset import Unsupported, check_class, check_function

# 编译器版本 ✅ —— 改动编译器时 ✅ 指纹要变 ✅（否则会复用旧产物 ✅ ✅）。
COMPILER_VERSION = "cratix-aot-1"


@dataclass
class FunctionPlan:
    name: str
    supported: bool
    reason: str = ""


@dataclass
class ModuleCompile:
    module_name: str
    init_symbol: str
    c_source: str = ""
    functions: list = field(default_factory=list)
    plans: list = field(default_factory=list)
    fallback_code: dict = field(default_factory=dict)
    classes: list = field(default_factory=list)
    module_ok: bool = True
    module_reason: str = ""
    key_names: list = field(default_factory=list)
    module_gen: object = None
    # **常量池** ✅（模块级字面量只创建一次 ✅）
    consts: object = None
    # **元数据** ✅：函数 -> {doc, sig} ✅（保真到 .pyd 里 ✅）
    func_meta: dict = field(default_factory=dict)
    # **类 / 方法的元数据** ✅
    class_meta: dict = field(default_factory=dict)
    # **生成器表达式的辅助函数源码** ✅（名字 -> 源码 ✅，模块 init 里建出来 ✅）
    genexp_sources: dict = field(default_factory=dict)
    # **模块级装饰器** ✅（(名字, [装饰器表达式...]) ✅）
    decorated_functions: list = field(default_factory=list)
    # **源码路径** ✅（`__file__` 保真用 ✅ —— 编译期就知道 ✅）
    source_path: str = ""
    # **类级回退** ✅（[(顺序, 源码)] ✅）
    fallback_classes: list = field(default_factory=list)
    # **模块体控制流里的类** ✅（(行, 列) -> 源码 ✅）
    inline_class_sources: dict = field(default_factory=dict)
    # **函数里"捕获外层局部"的类** ✅（(行, 列) -> 工厂函数 ✅）
    class_factories: dict = field(default_factory=dict)
    # **类工厂需要的"晚绑定"名字** ✅（函数名 -> 名字集合 ✅ —— 要进 cell 字典 ✅）
    late_captures: dict = field(default_factory=dict)
    # **模块体每条语句结束的行数** ✅（类按源码位置插入用 ✅）
    body_marks: list = field(default_factory=list)
    # **类的源码位置** ✅（(模块体第几条语句之后, 类名, 是否回退) ✅）
    class_order: list = field(default_factory=list)

    @property
    def supported_names(self) -> list:
        return [p.name for p in self.plans if p.supported]

    @property
    def fallback_names(self) -> list:
        return [p.name for p in self.plans if not p.supported]






def ir_node_params(names, defaults, params):
    """复制参数表并**前置 self** ✅（默认值右对齐 ✅ → 前面补 None ✅）。"""
    from . import ir

    return ir.node(
        "params",
        names=names,
        defaults=defaults,
        kwdefaults=list(params.get("kwdefaults", [])),
        kwonly=list(params.get("kwonly", [])),
        vararg=params.get("vararg"),
        kwarg=params.get("kwarg"),
    )

def _module_code_objects(source: str, filename: str) -> dict:
    """编译整个模块 ✅ 并取出顶层函数的 code object ✅（marshal 后的字节 ✅）。

    失败（编译错误 ✅）→ 返回空表 ✅（该模块整体回退 ✅）。
    """
    import marshal

    try:
        module_code = compile(source, filename, "exec")
    except Exception:  # noqa: BLE001 - 语法错误 ✅ 由上层处理 ✅
        return {}
    out: dict = {}
    for const in module_code.co_consts:
        if isinstance(const, type(module_code)) and const.co_name not in out:
            try:
                out[const.co_name] = marshal.dumps(const)
            except Exception:  # noqa: BLE001
                continue
    return out

def _compiler_source_digest() -> str:
    """**编译器自身的代码摘要** ✅ —— 改了编译器就该重编 ✅（#140 ✅）。

    `COMPILER_VERSION` 是**手写常量** ✅ —— 改代码忘了改它 ✅ 就会复用**旧的 .pyd** ✅
    （实测：命名空间清理之后重新构建 ✅ 探针拿到的还是旧产物 ✅ 报了一堆已经修好的"泄漏" ✅）。
    这里直接对**编译器包里的 .py** 取摘要 ✅ —— 改一个字节就变 ✅。
    """
    import hashlib as _hashlib

    root = Path(__file__).resolve().parent
    digest = _hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


COMPILER_SOURCE_DIGEST = _compiler_source_digest()


def fingerprint_for(module_name: str, source: Path, options: str = "") -> str:
    """模块的编译指纹 ✅（增量编译的依据 ✅）。

    指纹 = 源码内容 ✅ + 编译器版本 ✅ + 解释器身份 ✅ + 选项 ✅
    → 任何一个变了都要重编 ✅ ✅。
    """
    import hashlib
    import sys

    digest = hashlib.sha256()
    digest.update(Path(source).read_bytes())
    digest.update(COMPILER_VERSION.encode("utf-8"))
    # **编译器代码摘要** ✅ —— 只改代码不改版本号也能让缓存失效 ✅（#140 ✅）
    digest.update(COMPILER_SOURCE_DIGEST.encode("utf-8"))
    digest.update(module_name.encode("utf-8"))
    digest.update(sys.version.encode("utf-8"))
    digest.update(sys.implementation.cache_tag.encode("utf-8"))
    digest.update(str(sys.flags.optimize).encode("utf-8"))
    digest.update(options.encode("utf-8"))
    return digest.hexdigest()


def init_symbol_for(module_name: str) -> str:
    """唯一的 init 符号 ✅（PyInit_<末段> 会被同名模块冲突 ✅ —— 用哈希 ✅）。"""
    digest = hashlib.sha1(module_name.encode("utf-8")).hexdigest()[:10]
    return "PyInit_cratix_" + digest


def _literal_repr(node) -> str | None:
    """默认值的源码表示 ✅ —— 只接受**字面量** ✅（否则文本签名解析会失败 ✅）。"""
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    return repr(value)


def _text_signature(args: ast.arguments, module_level: bool = True) -> str:
    """生成 CPython 的 **__text_signature__** ✅（让 `inspect.signature` 可用 ✅）。

    约定 ✅：`$module` 表示模块级 C 函数 ✅（解析器据此跳过它 ✅）。
    """
    parts = []
    if module_level:
        parts.append("$module")
    else:
        # **方法** ✅：`$self` 是 CPython 的"这是绑定方法"标记 ✅ ——
        # 有它 inspect.signature(实例.方法) 才会把 self 去掉 ✅。
        parts.append("$self")
    positional = list(getattr(args, "posonlyargs", [])) + list(args.args)
    if not module_level and positional:
        # AST 里 self 是普通参数 ✅ -> 签名里由 `$self` 代表 ✅，这里跳过它 ✅
        # self 永远没有默认值 ✅ -> 只丢位置参数即可 ✅（defaults 保持右对齐 ✅）
        positional = positional[1:]
    defaults = list(args.defaults)
    pad = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        default_index = index - pad
        text = arg.arg
        if default_index >= 0:
            default = _literal_repr(defaults[default_index])
            if default is None:
                return ""  # 有非字面量默认值 ✅ -> 干脆不给签名 ✅（别给假的 ✅）
            text += "=" + default
        parts.append(text)
    if args.vararg is not None:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for index, arg in enumerate(args.kwonlyargs):
        text = arg.arg
        default_node = args.kw_defaults[index]
        if default_node is not None:
            default = _literal_repr(default_node)
            if default is None:
                return ""
            text += "=" + default
        parts.append(text)
    if args.kwarg is not None:
        parts.append("**" + args.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _doc_of(node) -> str:
    """原样取 docstring ✅（`clean=False` ✅ —— `__doc__` 就是原字符串 ✅）。"""
    try:
        return ast.get_docstring(node, clean=False) or ""
    except TypeError:
        return ""


def _source_segment(source: str, node, dedent: bool = False) -> str | None:
    """取一段源码 ✅（**带装饰器的函数要从装饰器那行开始** ✅）。

    为什么不用 `ast.get_source_segment` 直接了事 ✅：
    - FunctionDef 的 lineno 指向 `def` 那行 ✅ -> 装饰器会被漏掉 ✅
      -> 回退后函数**没有装饰器** ✅ -> 静默语义偏差 ✅；
    - 类方法在类体里**有缩进** ✅ -> 直接 exec 会 IndentationError ✅ -> 需要 dedent ✅。
    """
    if not source:
        return None
    lines = source.splitlines()
    decorators = getattr(node, "decorator_list", None) or []
    if decorators:
        start = min(d.lineno for d in decorators)
    else:
        start = node.lineno
    end = getattr(node, "end_lineno", None) or start
    text = chr(10).join(lines[start - 1:end])
    # **一律 dedent** ✅ —— 类方法/装饰器都有缩进 ✅；顶层代码 dedent 不改内容 ✅
    return textwrap.dedent(text).strip()


def _decorator_segment(source: str, node) -> str | None:
    """取**装饰器表达式本身** ✅（不含前导 `@` ✅ —— 运行期要的是表达式 ✅）。

    `Py_eval_input` 只接受**一个表达式** ✅ ——
    行切片会带上 `@` 与缩进 ✅ → IndentationError / SyntaxError ✅（实测 ✅）。
    """
    try:
        segment = ast.get_source_segment(source, node)
    except Exception:  # noqa: BLE001
        segment = None
    if not segment:
        segment = _source_segment(source, node)
        stripped = segment.lstrip()
        if stripped.startswith("@"):
            segment = stripped[1:]
    return textwrap.dedent(segment).strip()


def _module_level_supported(stmt) -> bool:
    """**模块级语句** ✅ —— 以前只放"简单赋值 + import" ✅，结果**真实模块几乎全被拒** ✅
    （实测：标准库 20 个模块只有 2 个能过 ✅ —— `if`/`try`/`expr` 全是拦路虎 ✅）。

    现在 ✅：模块体走的是**和函数体同一套**生成器 ✅（`module_gen` ✅），
    支持的就该让它编 ✅；真不支持的自然会 `Unsupported` -> 退回整模块回退 ✅（语义不变 ✅）。
    """
    if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr)):
        return True
    if isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
        return True
    if isinstance(stmt, (ast.Assert, ast.Delete)):
        return True
    if isinstance(stmt, ast.Import):
        return True
    # **`from ... import ...`** ✅ —— 以前这一条不认 ✅ -> 整个模块回退 ✅（真实代码几乎都中招 ✅）
    if isinstance(stmt, ast.ImportFrom):
        # **`from m import *`** ✅ 已支持 ✅（模块级 ✅）
        return True
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return True
    # **类不进模块语句表** ✅ —— 调用方已经先跳过 ✅；
    # 类由 codegen 在建模块时单独生成 ✅（见 compile_module 的类段 ✅）。
    return False


def _rewrite_class_source(segment: str, captured) -> str:
    """把类源码里对捕获名的**读取**改写成 `_cratix_cells["名字"]` ✅（晚绑定 ✅）。"""
    from .backend.codegen import _CellRewriter

    try:
        tree = ast.parse(segment)
        tree = _CellRewriter(captured).visit(tree)
        text = ast.unparse(ast.fix_missing_locations(tree))
    except Exception:  # noqa: BLE001 - 改写不了就回退 ✅
        return ""
    return text + chr(10)


def _bound_before(func_node, class_node) -> set:
    """**类定义那一刻已经绑定的名字** ✅（参数 + 之前赋过值的局部 ✅）。

    用行号做**保守**判断 ✅：只要有赋值语句在类定义之前 ✅ 就算"已绑定" ✅。
    """
    limit = getattr(class_node, "lineno", 0)
    # **参数** ✅（只算**本函数**的 ✅ —— 方法参数不算 ✅，实测踩过 ✅）
    bound: set = set()
    args = func_node.args
    for item in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        bound.add(item.arg)
    for item in (args.vararg, args.kwarg):
        if item is not None:
            bound.add(item.arg)

    def walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue  # **不进嵌套作用域** ✅
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                if getattr(child, "lineno", 0) < limit:
                    bound.add(child.id)
            walk(child)

    walk(func_node)
    return bound


def _deleted_locals(func_node) -> set:
    """**会被 `del` 的局部名** ✅（读它们时要检查 NULL ✅）。只算本层的 `del` ✅。"""
    names: set = set()
    for child in ast.walk(func_node):
        if not isinstance(child, ast.Delete):
            continue
        for target in child.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _nested_defs(func_node) -> list:
    """收集 `func_node` 里**直接属于它**的嵌套 def ✅（可以夹在 if/for/with/try 里 ✅，
    但不进更深一层的 def/lambda/class ✅ —— 那些归它们自己的父函数 ✅）。"""
    found: list = []

    def walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                found.append(child)
            elif isinstance(child, (ast.Lambda, ast.ClassDef, ast.AsyncFunctionDef)):
                continue
            else:
                walk(child)

    walk(func_node)
    return found


def compile_module(path: Path, module_name: str | None = None, const_pool: bool = True) -> ModuleCompile:
    """把一个 Python 模块编译成 C ✅（函数级判定 ✅）。

    **绝不把异常抛给调用方** ✅ —— 编译器再怎么样也不能**崩掉构建** ✅：
    任何没预料到的异常都变成"模块级回退" ✅，并在 reason 里标明是**内部错误** ✅（方便报 bug ✅）。
    """
    try:
        return _compile_module(path, module_name, const_pool)
    except Unsupported as exc:
        return ModuleCompile(
            module_name=module_name or Path(path).stem,
            init_symbol=init_symbol_for(module_name or Path(path).stem),
            module_ok=False,
            module_reason="未支持：" + exc.reason,
            source_path=str(path),
        )
    except Exception as exc:  # noqa: BLE001 - 编译器内部的任何异常都要降级 ✅
        return ModuleCompile(
            module_name=module_name or Path(path).stem,
            init_symbol=init_symbol_for(module_name or Path(path).stem),
            module_ok=False,
            module_reason="内部错误（请报 bug）：" + type(exc).__name__ + ": " + str(exc)[:120],
            source_path=str(path),
        )


def _compile_module(path: Path, module_name: str | None = None, const_pool: bool = True) -> ModuleCompile:
    """真正的编译实现 ✅（异常由 `compile_module` 兜底 ✅）。"""
    path = Path(path)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (UnicodeDecodeError, SyntaxError, ValueError) as exc:
        # **读不出来/解析不了** ✅ -> 明确的模块级回退 ✅（不能把异常抛给调用方 ✅）
        return ModuleCompile(
            module_name=module_name or path.stem,
            init_symbol=init_symbol_for(module_name or path.stem),
            module_ok=False,
            module_reason="源码不是合法的 UTF-8 Python：" + type(exc).__name__,
        )
    module_name = module_name or path.stem
    result = ModuleCompile(
        module_name=module_name,
        init_symbol=init_symbol_for(module_name),
        source_path=str(path),
    )

    # --- 模块级语句 ✅ ---
    module_stmts = []
    # **类的源码位置** ✅ —— 类必须**按源码顺序**创建 ✅（`csv.py` 那种
    # `register_dialect('excel', excel)` 就在模块体里用到了类 ✅）。
    class_order: list = []
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            # **类按源码位置插进模块体** ✅（前面已经执行了 len(module_stmts) 条语句 ✅）
            class_order.append((len(module_stmts), stmt.name, False))
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _module_level_supported(stmt):
            result.module_ok = False
            result.module_reason = "模块级语句不支持：" + type(stmt).__name__
            return result
        if isinstance(stmt, ast.Expr):
            continue  # 文档字符串 ✅ —— 无需编译 ✅
        module_stmts.append(stmt)

    # **就地 exec 的类** ✅ —— 两类 ✅：
    # 1. 模块体控制流里的类（`if cond: class C: ...` ✅）；
    # 2. **函数体里的类**（`def f(): class C: ...` ✅）—— 真实代码里非常多 ✅
    #    （`functools.cmp_to_key` ✅ / `csv.sniff` ✅ / `ctypes.CFUNCTYPE` ✅ ...）
    # 顶层类走"按源码位置创建" ✅，这两类走"就地 exec 源码" ✅（语义一致 ✅）。
    def _register_inline_class(node, scope=None, func_node=None) -> None:
        key = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
        if key in result.inline_class_sources or key in result.class_factories:
            return
        segment = _source_segment(source, node, dedent=True)
        if not segment:
            return
        if scope is not None:
            # **类体/方法引用了外层局部** ✅ -> 用**工厂函数** ✅：
            # 合成 `def _factory(捕获名...): <类源码>; return 类` ✅ 在模块 init 里 exec ✅，
            # 调用点带**实参**调用 ✅ —— 这样方法拿到的是**真闭包** ✅（语义与源码完全一致 ✅）。
            used = {
                sub.id for sub in ast.walk(node)
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
            }
            captured = sorted(used & (scope.locals_ - {"__class__"}))
            if captured:
                # **类定义时还没赋值的捕获变量** ✅ -> 工厂会把它当实参传 NULL ✅ -> 段错误 ✅
                # （实测：`class Guard:` 在 `log = []` **之前** ✅ 方法里用 `log` ✅ —— 这正是
                #  真实代码最常见的写法 ✅）。这类先**明确回退** ✅（语义正确 ✅）。
                bound = _bound_before(func_node, node) if func_node is not None else set()
                late = [name for name in captured if name not in bound]
                if late:
                    # **晚绑定** ✅ —— 类定义时这些名字还没赋值（`class C:` 写在 `x = ...` 之前 ✅）。
                    # 做法 ✅：把类源码里对它们的**读取**改写成 `_cratix_cells["名字"]` ✅，
                    # 工厂收 **cell 字典** 而不是值 ✅ -> 方法在**调用时**才取值 ✅（语义与闭包一致 ✅）。
                    rewritten = _rewrite_class_source(segment, captured)
                    if not rewritten:
                        return
                    factory = "_cratix_cls_factory_" + str(len(result.class_factories))
                    body = chr(10).join("    " + line for line in rewritten.splitlines())
                    result.class_factories[key] = {
                        "name": factory,
                        "captured": sorted(captured),
                        "class_name": node.name,
                        "cells": True,
                        "source": (
                            "def " + factory + "(_cratix_cells):" + chr(10)
                            + body + chr(10)
                            + "    return " + node.name + chr(10)
                        ),
                    }
                    if func_node is not None:
                        result.late_captures.setdefault(func_node.name, set()).update(captured)
                    return
                factory = "_cratix_cls_factory_" + str(len(result.class_factories))
                body = chr(10).join("    " + line for line in segment.splitlines())
                result.class_factories[key] = {
                    "name": factory,
                    "captured": captured,
                    "class_name": node.name,
                    "source": (
                        "def " + factory + "(" + ", ".join(captured) + "):" + chr(10)
                        + body + chr(10)
                        + "    return " + node.name + chr(10)
                    ),
                }
                return
        result.inline_class_sources[key] = segment

    for stmt in module_stmts:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.ClassDef):
                _register_inline_class(sub)


    # --- 模块级绑定的名字 ✅（= 函数里的模块全局 ✅）---
    # **与黄金模型共用同一实现** ✅ —— 否则两边判定会漂移 ✅ ✅
    # **模块自带的 dunder** ✅（`__file__` 我们在 init 里补上 ✅）—— 不认它们的话 ✅
    # 用到 `__file__` 的函数会被误判成"闭包捕获" ✅ 整函数回退 ✅。
    module_globals = module_bindings(tree) | {
        "__file__", "__name__", "__doc__", "__package__", "__loader__", "__spec__",
    }
    # **函数体里的类** ✅（要等 module_globals 算好才能做作用域分析 ✅）
    for stmt in tree.body:
        funcs = []
        if isinstance(stmt, ast.FunctionDef):
            funcs = [stmt]
        elif isinstance(stmt, ast.ClassDef):
            funcs = [e for e in stmt.body if isinstance(e, ast.FunctionDef)]
        for func_node in funcs:
            func_scope = analyze_function(func_node, module_globals)
            for sub in ast.walk(func_node):
                if isinstance(sub, ast.ClassDef) and sub is not func_node:
                    _register_inline_class(sub, func_scope, func_node)
    # **`from m import *`** ✅：那些名字在**编译期**就要知道 ✅（否则函数里用到会被当成闭包自由变量 ✅）。
    # 直接把模块导进来取 `__all__` / `dir()` ✅ —— 运行期本来也要导入它 ✅。
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        if not any(alias.name == "*" for alias in stmt.names):
            continue
        try:
            base = module_name.rpartition(".")[0]
            for _ in range(max(0, stmt.level - 1)):
                base = base.rpartition(".")[0]
            absolute = (base + "." + (stmt.module or "")) if stmt.module else base
            if stmt.level == 0:
                absolute = stmt.module or ""
            imported = importlib.import_module(absolute)
            exported = getattr(imported, "__all__", None)
            module_globals |= set(exported if exported is not None else dir(imported))
        except Exception:  # noqa: BLE001 - 导不进来就算了 ✅（运行期会自然报错 ✅）
            continue

    # --- 类槽位预扫 ✅（函数生成时就要知道"哪些类有哪些槽位"）---
    class_slots_map: dict = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.ClassDef):
            continue
        # **继承的类不做槽位** ✅（布局是基类的 ✅ -> 按偏移读写会越界 ✅）
        if stmt.bases or getattr(stmt, "keywords", None):
            class_slots_map[stmt.name] = {}
            continue
        writes = set()
        for entry in stmt.body:
            if not isinstance(entry, ast.FunctionDef):
                continue
            for child in ast.walk(entry):
                if isinstance(child, ast.Assign):
                    targets = list(child.targets)
                elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                    targets = [child.target]
                else:
                    continue
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        writes.add(target.attr)
        names = sorted(n for n in writes if n.isidentifier() and n not in ("self", "__class__"))
        class_slots_map[stmt.name] = {name: index for index, name in enumerate(names)}

    # **类级回退** ✅ —— 单个类不支持（继承 ✅ / 类体语句 ✅）不该拖垮整个模块 ✅：
    # 把类源码在模块 init 里 exec 一遍 ✅（语义与源码一致 ✅），其余函数照常 AOT ✅。
    fallback_classes: list = []
    class_names = {stmt.name for stmt in tree.body if isinstance(stmt, ast.ClassDef)}
    # **顺序风险** ✅：类在模块体之后创建 ✅ -> 若模块体里就用到类的名字 ✅ 顺序就错了 ✅
    def _unused_module_scope_names(node) -> set:  # noqa: D401 - 保留历史实现，不再使用
        """只算**模块级真的会执行**的名字 ✅ —— 不进 def/lambda/class 体 ✅（那里不立刻执行 ✅）。"""
        found: set = set()

        def walk(item) -> None:
            for child in ast.iter_child_nodes(item):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    found.add(child.id)
                walk(child)

        walk(node)
        return found

    # **不再需要"模块体引用类"的拦截** ✅ —— 类现在**按源码位置**创建 ✅

    # **模块级装饰器** ✅（名字 -> 装饰器表达式 ✅）
    decorated_functs: list = []

    # --- 闭包 ✅：函数里的嵌套 def ✅（cell 字典 + functools.partial ✅）---
    inner_gens: list = []
    inner_sites: dict = {}
    inner_cells: dict = {}
    # **被内层 `nonlocal` 写过的名字** ✅（外层只住 cell 字典 ✅）
    inner_written: dict = {}
    # **模块级控制流里的 def 节点** ✅（它们的体里还可能再嵌套 ✅ —— 扫描要用 ✅）
    mdef_nodes: list = []
    # **扫描"谁里面可能有嵌套 def"** ✅ —— 顶层函数 ✅ 类方法 ✅
    # **以及模块级控制流里的 def** ✅（它们自己的体里也可能再嵌套 ✅ —— 实测踩过 ✅）
    _owners: list = []
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef):
            _owners.append(stmt)
        elif isinstance(stmt, ast.ClassDef):
            _owners.extend(entry for entry in stmt.body if isinstance(entry, ast.FunctionDef))
    for _node in mdef_nodes:
        _owners.append(_node)
    if True:
        for func_node in _owners:
            outer = analyze_function(func_node, module_globals)
            # **受控流里包着的嵌套 def 也算** ✅（`if`/`for`/`with`/`try` 里面定义的 ✅）
            for child in _nested_defs(func_node):
                if not isinstance(child, ast.FunctionDef):
                    continue
                inner_scope = analyze_function(child, module_globals)
                captured = inner_scope.free & outer.locals_
                key = (getattr(child, "lineno", 0), getattr(child, "col_offset", 0))
                if key in inner_sites:
                    continue
                # **`nonlocal` 写回** ✅ / 更深嵌套 ✅ -> 暂不支持 ✅（回退 ✅ 语义仍对 ✅）
                writes = {sub.id for sub in ast.walk(child) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)}
                # **`nonlocal` 写回** ✅：内层会改外层变量 ✅ -> 这些名字在外层必须
                # **只住 cell 字典** ✅（否则外层读到的是**过期副本** ✅）。照常登记 ✅，
                # 并记下"被内层写过"的名字 ✅（外层生成时传 `cells_only` ✅）。
                written_back = writes & captured
                if written_back:
                    inner_written.setdefault(func_node.name, set()).update(written_back)
                if any(
                    isinstance(sub, ast.FunctionDef) and sub is not child
                    for sub in ast.walk(child)
                ):
                    continue
                name = "_cratix_inner_" + str(len(inner_sites))
                child._cratix_owner = func_node.name
                inner_sites[key] = {
                    "name": name,
                    "def_name": child.name,
                    "captures": sorted(captured),
                }
                inner_gens.append((name, child, captured))
    # **按"外层函数名"登记** ✅ —— 生成外层时要告诉它"哪些名字住进 cell 字典" ✅
    for _name, _node, _captured in inner_gens:
        owner = getattr(_node, "_cratix_owner", None)
        if owner is None:
            continue
        inner_cells.setdefault(owner, set()).update(_captured)

    # **生成器表达式的捕获** ✅（(行, 列) -> 捕获的名字 ✅）——
    # 有捕获时合成函数多一个 `_cratix_cells` 首参 ✅，用 `functools.partial` 绑上 ✅。
    genexp_captures: dict = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef):
            genexp_funcs = [stmt]
        elif isinstance(stmt, ast.ClassDef):
            genexp_funcs = [e for e in stmt.body if isinstance(e, ast.FunctionDef)]
        else:
            continue
        for func_node in genexp_funcs:
            outer = analyze_function(func_node, module_globals)
            for child in ast.walk(func_node):
                if not isinstance(child, ast.GeneratorExp):
                    continue
                key = (getattr(child, "lineno", 0), getattr(child, "col_offset", 0))
                if key in genexp_captures:
                    continue
                targets = set()
                for gen in child.generators:
                    for sub in ast.walk(gen.target):
                        if isinstance(sub, ast.Name):
                            targets.add(sub.id)
                used = {sub.id for sub in ast.walk(child) if isinstance(sub, ast.Name)}
                captured = (used - targets) & outer.locals_
                if captured:
                    genexp_captures[key] = sorted(captured)
                    inner_cells.setdefault(func_node.name, set()).update(captured)

    # **类工厂的晚绑定名字 -> cell 字典** ✅（必须在建 gen 之前合并 ✅）
    for _owner, _names in result.late_captures.items():
        inner_cells.setdefault(_owner, set()).update(_names)

    # --- lambda ✅：先扫出来 ✅，编译成**模块级原生函数** ✅（使用点按名字取 ✅）---
    lambda_gens: list = []
    lambda_names: dict = {}
    lambda_captures: dict = {}
    # **模块级控制流里的 def** ✅（(行, 列) -> {内部名, 公开名} ✅）
    mdef_names: dict = {}
    # **没法编译的 lambda** ✅（外层函数用到它时必须回退 ✅ 不能静默 ✅）
    lambda_blocked: set = set()
    # **模块级的 lambda** ✅ —— 以前只扫函数/方法体 ✅ ->
    # `X = lambda v: ...` 这种模块级语句会 "lambda（未登记）" ✅ -> **整个模块**回退 ✅（实测 21 个 ✅）
    # **模块级控制流里的 `def`** ✅ —— `if cond: def f(): ...` 这种 ✅
    # 以前走到模块体生成就 "嵌套定义（未登记）" ✅ -> **整个模块**回退 ✅（实测 54 个 ✅）。
    # 做法与 lambda 一致 ✅：编成**模块级原生函数** ✅，在 def 语句处把**公开名**绑上去 ✅。
    for stmt in module_stmts:
        for child in ast.walk(stmt):
            if not isinstance(child, ast.FunctionDef):
                continue
            key = (getattr(child, "lineno", 0), getattr(child, "col_offset", 0))
            if key in mdef_names:
                continue
            try:
                built = builder.build_function(child)
            except Unsupported as exc:
                result.plans.append(FunctionPlan(child.name, False, exc.reason))
                continue
            name = "_cratix_mdef_" + str(len(mdef_names))
            mdef_names[key] = {"name": name, "public": child.name}
            mdef_nodes.append(child)
            lambda_names[key] = name
            lambda_captures[key] = []
            lambda_gens.append((name, built, []))

    for stmt in module_stmts:
        for child in ast.walk(stmt):
            if not isinstance(child, ast.Lambda):
                continue
            key = (getattr(child, "lineno", 0), getattr(child, "col_offset", 0))
            if key in lambda_names:
                continue
            try:
                built = builder.build_lambda(child)
            except Unsupported as exc:
                result.plans.append(FunctionPlan("_cratix_lambda_mod", False, exc.reason))
                continue
            name = "_cratix_lambda_" + str(len(lambda_names))
            lambda_names[key] = name
            lambda_captures[key] = []
            lambda_gens.append((name, built, []))

    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.ClassDef)):
            continue
        nodes = [stmt]
        if isinstance(stmt, ast.ClassDef):
            nodes = [entry for entry in stmt.body if isinstance(entry, ast.FunctionDef)]
        for func_node in nodes:
            for child in ast.walk(func_node):
                if not isinstance(child, ast.Lambda):
                    continue
                key = (getattr(child, "lineno", 0), getattr(child, "col_offset", 0))
                if key in lambda_names:
                    continue
                # **捕获检查** ✅：lambda 读到外层局部变量必须回退 ✅（否则会变成"看不见"→ NameError ✅）
                analysis = analyze_function(func_node, module_globals)
                params = builder.build_params(child.args)
                # **params 是 IR Node** ✅ -> 一律用 `.get` ✅（`params["vararg"]` 会 TypeError ✅ 实测 ✅）
                param_names = set(params.get("names", [])) | set(params.get("kwonly", []))
                if params.get("vararg"):
                    param_names.add(params.get("vararg"))
                if params.get("kwarg"):
                    param_names.add(params.get("kwarg"))
                used = {
                    sub.id for sub in ast.walk(child)
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
                }
                captured = (used - param_names) & analysis.locals_
                name = "_cratix_lambda_" + str(len(lambda_names))
                try:
                    built = builder.build_lambda(child)
                except Unsupported as exc:
                    # **lambda 里遇到不支持的语法** ✅ -> 这个 lambda 回退 ✅（外层函数用到它会回退 ✅）
                    result.plans.append(FunctionPlan(name, False, exc.reason))
                    lambda_blocked.add(key)
                    continue
                lambda_names[key] = name
                # **捕获外层局部** ✅ -> 和嵌套 def 同一套 cell 机制 ✅
                lambda_gens.append((name, built, sorted(captured)))
                if captured:
                    lambda_captures[key] = sorted(captured)
                    inner_cells.setdefault(func_node.name, set()).update(captured)

    # --- 函数级判定 + 生成 ✅ ---
    key_names = []
    # **常量池** ✅ —— 整模块一个 ✅（常量只建一次 ✅，循环里只 INCREF ✅）
    # const_pool=False 只用于 **A/B 基准对照** ✅（产品路径永远开 ✅）
    consts = codegen.ConstantPool() if const_pool else None
    # **嵌套 def 也先生成** ✅（模块级原生函数 ✅；捕获的变量作为**隐藏首参** `_cratix_cells` ✅）
    for inner_name, inner_node, inner_captured in inner_gens:
        try:
            inner_built = builder.build_function(inner_node)
        except Unsupported as exc:
            # **嵌套 def 里遇到不支持的语法** ✅ -> 只要这个 def 回退 ✅ **不能拖垮整个模块** ✅
            # （实测：嵌套的可调用体里有 `yield` 时以前会直接抛出去 ✅ -> 整个模块编译失败 ✅）
            result.plans.append(FunctionPlan(inner_name, False, exc.reason))
            continue
        raw_params = inner_built["params"]
        # **隐藏首参 = cell 字典** ✅（`functools.partial` 会把它绑在前面 ✅）——
        # 没有捕获时**不加** ✅（否则用户参数会错位 ✅ -> "missing required argument" ✅ 实测 ✅）
        hidden = ["_cratix_cells"] if inner_captured else []
        inner_params = {
            "names": hidden + list(raw_params.get("names", [])),
            "defaults": list(raw_params.get("defaults", [])),
            "kwonly": list(raw_params.get("kwonly", [])),
            "kwdefaults": list(raw_params.get("kwdefaults", [])),
            "vararg": raw_params.get("vararg"),
            "kwarg": raw_params.get("kwarg"),
        }
        inner_scope = analyze_function(inner_node, module_globals)
        inner_scope.free = inner_scope.free - set(inner_captured)
        # **隐藏首参也是局部变量** ✅ —— 不登记的话 C 里不会声明这个变量 ✅
        if inner_captured:
            inner_scope.locals_ = set(inner_scope.locals_) | {"_cratix_cells"}
        try:
            inner_gen = codegen.FunctionGen(
                inner_name, inner_params, inner_built["body"], inner_scope, consts,
                lambda_names=lambda_names,
                inner_sites=inner_sites,
                closure_names=set(inner_captured),
                package=module_name.rpartition(".")[0],
            )
            inner_gen.inline_class_sources = dict(result.inline_class_sources)
            inner_gen.class_factories = dict(result.class_factories)
            inner_gen.emit_binding()
            inner_gen.emit_cells_init()
            for inner_stmt in inner_built["body"]:
                inner_gen.emit_stmt(inner_stmt)
        except Unsupported as exc:
            result.plans.append(FunctionPlan(inner_name, False, exc.reason))
            continue
        result.functions.append(inner_gen)
        for pname in list(inner_params.get("names", [])) + list(inner_params.get("kwonly", [])):
            key_names.append(pname)
        key_names.extend(sorted(inner_gen.global_names))
        result.genexp_sources.update(inner_gen.genexp_sources)
        result.plans.append(FunctionPlan(inner_name, True, ""))

    # **lambda 先生成** ✅ —— 它们是模块级函数 ✅，函数体里只按名字取 ✅
    for lam_name, built_lambda, lam_captured in lambda_gens:
        lam_params = built_lambda["params"]
        lam_scope = Scope(name=lam_name)
        lam_scope.locals_ = set(lam_params.get("names", [])) | set(lam_params.get("kwonly", []))
        if lam_params.get("vararg"):
            lam_scope.locals_.add(lam_params.get("vararg"))
        if lam_params.get("kwarg"):
            lam_scope.locals_.add(lam_params.get("kwarg"))
        # **有捕获就把 cell 字典做成隐藏首参** ✅
        if lam_captured:
            lam_params = {
                "names": ["_cratix_cells"] + list(lam_params.get("names", [])),
                "defaults": list(lam_params.get("defaults", [])),
                "kwonly": list(lam_params.get("kwonly", [])),
                "kwdefaults": list(lam_params.get("kwdefaults", [])),
                "vararg": lam_params.get("vararg"),
                "kwarg": lam_params.get("kwarg"),
            }
            lam_scope.locals_.add("_cratix_cells")
        try:
            lam_gen = codegen.FunctionGen(
                lam_name, lam_params, built_lambda["body"], lam_scope, consts,
                lambda_names=lambda_names,
                closure_names=set(lam_captured),
                inner_sites=inner_sites,
                package=module_name.rpartition(".")[0],
            )
            lam_gen.inline_class_sources = dict(result.inline_class_sources)
            lam_gen.class_factories = dict(result.class_factories)
            lam_gen.emit_binding()
            lam_gen.emit_cells_init()
            for lam_stmt in built_lambda["body"]:
                lam_gen.emit_stmt(lam_stmt)
        except Unsupported as exc:
            result.plans.append(FunctionPlan(lam_name, False, exc.reason))
            lambda_names.pop(
                next((k for k, v in lambda_names.items() if v == lam_name), None), None
            )
            continue
        result.functions.append(lam_gen)
        # **参数名要 intern** ✅ —— 关键字绑定要用 `cratix_key_v_<名字>` ✅
        for pname in list(lam_params.get("names", [])) + list(lam_params.get("kwonly", [])):
            key_names.append(pname)
        key_names.extend(sorted(lam_gen.global_names))
        result.genexp_sources.update(lam_gen.genexp_sources)
        result.plans.append(FunctionPlan(lam_name, True, ""))

    # **函数级回退的字节码来源** ✅：编译整个模块 ✅ 从 co_consts 里取函数 code object ✅
    # （比"抠源码再编译"稳 ✅ —— 行号/闭包/默认值都保真 ✅）
    # 保留 helper ✅（未来可用于字节码级回退 ✅）—— 当前用**内嵌源码** ✅
    for stmt in tree.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        scope = analyze_function(stmt, module_globals)
        try:
            # **`nonlocal` 写回** ✅ 现在支持了 ✅（内层写 cell ✅、外层只住 cell 字典 ✅）——
            # 这里只拦"**本函数自己**声明了 nonlocal 又自己写"的怪情况 ✅（那说明它是嵌套函数 ✅）。
            # **不能扫整棵子树** ✅：内层 def 的写回是正常的 ✅（以前扫子树 -> 外层被误判回退 ✅ 实测 ✅）。
            # **不要在这里判 `nonlocal` 写回** ✅ —— `scope.nonlocals_` 会**扫到嵌套 def** ✅，
            # 从而把**外层**函数误判成"自己写了 nonlocal" ✅（实测 ✅）。
            # 真正的语义由 cell 字典保证 ✅（内层写 ✅、外层只读字典 ✅）；非法的 `nonlocal`
            # 本来就过不了 `ast.parse`/编译 ✅。
            # **模块级函数没有外层作用域** ✅ -> 它读到的"自由名"**只可能是全局** ✅
            # （以前把这些名字当成"闭包捕获" ✅ -> 整个函数回退 ✅ —— 实测 `calendar.py` 的
            #  `FEBRUARY`（`globals().update(...)` 动态绑定 ✅）就中招 ✅）
            scope.free = set()
            # **模块级装饰器走函数级回退** ✅ ——
            # 原生函数是 PyCFunction ✅（没有 __dict__ ✅）✅ -> functools.wraps 之类会在它上面失败 ✅；
            # 回退路径在模块字典里 exec 源码 ✅ -> 装饰器**原样生效** ✅（无静默语义偏差 ✅）。
            # **模块级装饰器** ✅：原生实现照常编译 ✅，运行期用**跳板**包一层再套装饰器 ✅
            # （原生 PyCFunction 没有 __dict__ ✅ -> functools.wraps 之类会在它上面失败 ✅）
            if stmt.decorator_list:
                decorated_functs.append((
                    stmt.name,
                    [_decorator_segment(source, dec) for dec in stmt.decorator_list],
                ))
            check_function(stmt, scope)
            built = builder.build_stmt(stmt)
            gen = codegen.FunctionGen(
                stmt.name, built.get("params"), built.get("body", []), scope, consts,
                known_classes=class_slots_map,
                # **函数里的相对导入** ✅ 也要能解析 ✅（否则 `from . import x` 回退 ✅）
                package=module_name.rpartition(".")[0],
                lambda_names=lambda_names,
                lambda_captures=lambda_captures,
                cell_names=inner_cells.get(stmt.name, set()),
                cells_only=inner_written.get(stmt.name, set()),
                inner_sites=inner_sites,
                genexp_captures=genexp_captures,
            )
            # **函数体里的类** ✅ 也要能查到源码 ✅
            gen.inline_class_sources = dict(result.inline_class_sources)
            gen.class_factories = dict(result.class_factories)
            gen.deleted_locals = _deleted_locals(stmt)
            gen.emit_binding()
            gen.emit_cells_init()
            for body_stmt in built.get("body", []):
                gen.emit_stmt(body_stmt)
        except Unsupported as exc:
            result.plans.append(FunctionPlan(stmt.name, False, exc.reason))
            # **函数级回退** ✅：把该函数的 code object marshal 后内嵌 ✅
            # → 运行期由 PyEval_EvalCode 执行 ✅（真 frame ✅ 真 traceback ✅ 语义等同源码 ✅）。
            # **内嵌函数源码** ✅（运行期 Py_CompileString 编译 ✅）——
            # 比内嵌 code object 简单 ✅（避开 marshal 的 C API ✅）且可读 ✅。
            segment = _source_segment(source, stmt)
            if segment:
                result.fallback_code[stmt.name] = {
                    "source": segment,
                    "filename": str(path),
                    # **源码顺序** ✅ —— 装饰器引用必须在定义之后执行 ✅（按名字排会 NameError ✅）
                    "order": stmt.lineno,
                }
            continue
        result.functions.append(gen)
        result.genexp_sources.update(gen.genexp_sources)
        result.plans.append(FunctionPlan(stmt.name, True, ""))
        # **元数据保真** ✅：docstring + 文本签名 ✅
        result.func_meta[stmt.name] = {
            "doc": _doc_of(stmt),
            "sig": _text_signature(stmt.args, module_level=True),
        }
        for name in list(gen.params.get("names", [])) + list(gen.params.get("kwonly", [])):
            key_names.append(name)
        # **全局名字的静态键** ✅
        key_names.extend(sorted(gen.global_names))

    # --- 类 ✅（v1：简单 object 子类 ✅）---
    for stmt in tree.body:
        if not isinstance(stmt, ast.ClassDef):
            continue
        try:
            check_class(stmt)
        except Unsupported as exc:
            # **类级回退** ✅ —— 这个类编译不了 ✅（继承 ✅ / 类体语句 ✅），
            # 用**源码 exec** ✅ 保住语义 ✅，别的类与函数照常 AOT ✅（不再整个模块回退 ✅）
            segment = _source_segment(source, stmt)
            if segment:
                fallback_classes.append((stmt.lineno, segment))
                class_order = [
                    (pos, name, True if name == stmt.name else is_fb)
                    for (pos, name, is_fb) in class_order
                ]
                result.plans.append(FunctionPlan(stmt.name, False, exc.reason))
                continue
            result.module_ok = False
            result.module_reason = exc.reason
            return result
        # **直调优化需要的静态事实** ✅：
        # 1. 本类**已经生成完**的方法 ✅（名字 -> C 函数名 ✅ —— 只认已生成的 ✅，避免引用未定义符号 ✅）；
        # 2. 被 `self.X = ...` 写过的属性 ✅（写过的**不能**直调同名方法 ✅）。
        generated_methods: dict = {}
        self_attr_writes = set()
        for entry in stmt.body:
            if not isinstance(entry, ast.FunctionDef):
                continue
            for child in ast.walk(entry):
                if isinstance(child, ast.Assign):
                    targets = list(child.targets)
                elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                    targets = [child.target]
                else:
                    continue
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        self_attr_writes.add(target.attr)
        # **哪些方法可以做成原生方法描述符** ✅：无装饰器的 ✅（装饰器要的是"可调用对象" ✅）
        descriptor_methods = [
            entry.name for entry in stmt.body
            if isinstance(entry, ast.FunctionDef) and not entry.decorator_list
        ]
        # **继承** ✅：非 object 基类 -> 类**不用槽位** ✅（实例布局由基类决定 ✅），
        # 创建走 `type(name, (Base,), ns)` ✅（这样 `slot_tp_init` 才会调我们的 `__init__` ✅）。
        inherited = bool(stmt.bases) or bool(getattr(stmt, "keywords", None))
        # **基类表达式里的名字要 intern** ✅（`cratix_key_v_Exception` ✅ —— 生成基类表达式时要用 ✅）
        for base in stmt.bases:
            for sub in ast.walk(base):
                if isinstance(sub, ast.Name):
                    key_names.append(sub.id)
        class_info = {
            "name": stmt.name,
            "inherited": inherited,
            "bases": [builder.build_expr(base) for base in stmt.bases],
            "doc": _doc_of(stmt),
            "descriptor_methods": [],
            # **只有注解的类体条目** ✅（`x: int` ✅ —— 只进 `__annotations__` ✅）
            "ann_only": [],
            # **回退方法** ✅（(名字, 源码, 行号) ✅ —— 建类时 exec 出来挂到类上 ✅）
            "fallback_methods": [],
            # **类体里的 import** ✅（源码 ✅ —— 建类时 exec 并把名字挂成类属性 ✅）
            "imports": [],
            # **类体里的类** ✅（(名字, 顺序, 源码) ✅）
            "inner_classes": [],
            # **属性槽位** ✅（`self.X = ...` 写过的名字 ✅ -> 实例结构体里的字段 ✅）
            "slots": sorted(class_slots_map.get(stmt.name, {})),
            "methods": [],
            "attrs": [],
            # **类装饰器** ✅（建类之后按**逆序**应用 ✅ —— 与 CPython 一致 ✅）
            "decorators": [seg for seg in (_decorator_segment(source, d) for d in stmt.decorator_list) if seg],
            # **方法装饰器** ✅（同样按逆序应用 ✅）
            "method_decorators": {},
        }
        # **类体里遇到不支持的东西** ✅ -> 只把这个类降级成源码 exec ✅（不能拖垮整个模块 ✅）
        class_broken = ""
        for item in stmt.body:
            if isinstance(item, ast.FunctionDef):
                if item.decorator_list:
                    segments = [seg for seg in (_decorator_segment(source, d) for d in item.decorator_list) if seg]
                    if segments:
                        class_info["method_decorators"][item.name] = segments
                method_scope = analyze_function(item, module_globals)
                # **方法也一样** ✅：方法**看不见类作用域** ✅（Python 语义 ✅）-> 自由名就是全局 ✅
                method_scope.free = set()
                try:
                    check_function(item, method_scope)
                    built = builder.build_stmt(item)
                    params = built.get("params")
                    # **self 本来就是 AST 里的普通参数** ✅ —— 不要再前置 ✅
                    # （前置过一次 ✅ → 参数表变成 ["self","self","x","y"] ✅ → 绑定全错 ✅）
                    self_params = params
                    # **C 函数名带类名** ✅ —— 不同类的同名方法不会撞符号 ✅
                    gen = codegen.FunctionGen(
                        stmt.name + "." + item.name,
                        self_params,
                        built.get("body", []),
                        method_scope,
                        consts,
                        class_methods=generated_methods,
                        self_attr_writes=self_attr_writes,
                        method_mode=item.name in set(descriptor_methods),
                        known_classes=class_slots_map,
                        enclosing_class=stmt.name,
                        lambda_names=lambda_names,
                        lambda_captures=lambda_captures,
                        cell_names=inner_cells.get(item.name, set()),
                    cells_only=inner_written.get(stmt.name, set()),
                        inner_sites=inner_sites,
                        genexp_captures=genexp_captures,
                        # **类方法里的相对导入** ✅ 也要有包名 ✅
                        package=module_name.rpartition(".")[0],
                    )
                    gen.inline_class_sources = dict(result.inline_class_sources)
                    gen.class_factories = dict(result.class_factories)
                    gen.emit_binding()
                    gen.emit_cells_init()
                    for body_stmt in built.get("body", []):
                        gen.emit_stmt(body_stmt)
                except Unsupported as exc:
                    result.plans.append(FunctionPlan(item.name, False, exc.reason))
                    # **类方法在类体里有缩进** ✅ -> 回退必须 dedent ✅ ✅
                    segment = _source_segment(source, item, dedent=True)
                    # **只回退这个方法** ✅（挂到类上 ✅ —— 不再拖累整个类 ✅）。
                    # **dunder 不行** ✅：槽位类的 `__init__`/`__getattr__` 等由 `tp_*` 槽调用 ✅，
                    # 挂个 Python 函数进去也不会被调到 ✅ -> 那些一律整类回退 ✅。
                    is_dunder = item.name.startswith("__") and item.name.endswith("__")
                    # **槽位类**（无基类 ✅）的 dunder 由 `tp_*` 槽调用 ✅ -> 挂 Python 函数不会被调到 ✅；
                    # **继承类**（`type(...)` 建的 ✅）的 dunder 走普通属性查找 ✅ -> 单独回退**可以工作** ✅。
                    if segment and (inherited or not is_dunder):
                        class_info["fallback_methods"].append((item.name, segment, item.lineno))
                    elif segment:
                        class_broken = "类方法回退：" + item.name + "（槽位类的 dunder）"
                        break
                    continue
                # **类方法不放进模块级函数表** ✅（否则会重复生成 ✅ + 出现在模块字典里 ✅）
                class_info["methods"].append((item.name, gen))
                # **生成成功后**才登记 ✅（后面生成的兄弟方法可以直调它 ✅）
                # **只登记描述符方法** ✅ —— 跳板方法（带装饰器）的参数里含 self ✅，
                # 直调会少传一个参数 ✅（所以它们一律不直调 ✅）。
                if item.name in set(descriptor_methods):
                    class_info["descriptor_methods"].append(item.name)
                    generated_methods[item.name] = "cratix_fn_" + codegen.c_ident(stmt.name + "." + item.name)
                result.genexp_sources.update(gen.genexp_sources)
                # **方法元数据** ✅：doc + 类名限定的 qualname ✅
                result.func_meta[stmt.name + "." + item.name] = {
                    "doc": _doc_of(item),
                    # **方法签名** ✅：`$self` 标记 ✅ -> inspect.signature(实例.方法) 会把 self 去掉 ✅
                    "sig": _text_signature(item.args, module_level=False),
                    "qualname": stmt.name + "." + item.name,
                    "cls": stmt.name,
                }
                # **方法参数也要 intern 键** ✅（绑定代码要用 ✅）
                for pname in list(self_params.get("names", [])) + list(self_params.get("kwonly", [])):
                    key_names.append(pname)
                key_names.extend(sorted(gen.global_names))
                result.plans.append(FunctionPlan(item.name, True, ""))
            elif isinstance(item, ast.AsyncFunctionDef):
                # **`async` 方法** ✅ —— 编译不了 ✅ 但可以**只回退它自己** ✅（挂到类上 ✅）。
                result.plans.append(FunctionPlan(item.name, False, "async 方法（回退成源码 ✅）"))
                segment = _source_segment(source, item, dedent=True)
                if segment:
                    class_info["fallback_methods"].append((item.name, segment, item.lineno))
                continue
            elif isinstance(item, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
                # **类体里的控制流** ✅（`if sys.version_info >= ...: def foo(...)` 这种 ✅）——
                # 与类体 import 同一套：exec 到临时命名空间 ✅，再把名字抄成类属性 ✅。
                # 里面的方法会变成**普通 Python 函数** ✅（语义正确 ✅ 只是没 AOT ✅）。
                segment = _source_segment(source, item, dedent=True)
                if segment:
                    class_info["imports"].append(segment)
                continue
            elif isinstance(item, ast.ImportFrom):
                # **类体里的 `from x import y`** ✅ —— 名字会变成**类属性** ✅。
                # 记下源码 ✅，建类时 exec 到一个临时命名空间 ✅，再把里面所有名字挂到类上 ✅。
                segment = _source_segment(source, item, dedent=True)
                if segment:
                    class_info["imports"].append(segment)
                continue
            elif isinstance(item, ast.ClassDef):
                # **类体里的类** ✅ —— 用**源码 exec** ✅（在 maker 里建完之后挂到外层类上 ✅）
                inner_source = _source_segment(source, item, dedent=True)
                if not inner_source:
                    raise Unsupported("类体里的类（取不到源码）")
                class_info["inner_classes"].append((item.name, item.lineno, inner_source))
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                # **类属性** ✅（含带注解的 `x: int = 0` ✅ —— 真实代码里到处是 ✅）
                if isinstance(item, ast.AnnAssign):
                    if not isinstance(item.target, ast.Name):
                        class_broken = "类体语句：AnnAssign（目标不是简单名字）"
                        break
                    if item.value is None:
                        # **只有注解、没有值** ✅（`x: int` ✅）—— 它**不建属性** ✅，
                        # 只往 `__annotations__` 里加一条 ✅。记下源码 ✅，建类时统一 exec 一次 ✅。
                        segment = _source_segment(source, item, dedent=True)
                        if segment:
                            class_info["ann_only"].append(segment)
                        continue
                    class_info["attrs"].append((item.target.id, builder.build_expr(item.value)))
                else:
                    target = item.targets[0]
                    if isinstance(target, ast.Name):
                        class_info["attrs"].append((target.id, builder.build_expr(item.value)))
                    elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(
                        item.value, (ast.Tuple, ast.List)
                    ) and len(target.elts) == len(item.value.elts) and all(
                        isinstance(elt, ast.Name) for elt in target.elts
                    ):
                        # **类体里的元组解包赋值** ✅（`a, b = 1, 2` ✅）—— 逐个变成类属性 ✅
                        for name_node, value_node in zip(target.elts, item.value.elts):
                            class_info["attrs"].append((name_node.id, builder.build_expr(value_node)))
                    else:
                        class_broken = "类属性赋值目标不是简单名字"
                        break
        if class_broken:
            segment = _source_segment(source, stmt)
            if segment:
                fallback_classes.append((stmt.lineno, segment))
                class_order = [
                    (pos, name, True if name == stmt.name else is_fb)
                    for (pos, name, is_fb) in class_order
                ]
                result.plans.append(FunctionPlan(stmt.name, False, class_broken))
                continue
            result.module_ok = False
            result.module_reason = class_broken
            return result
        result.classes.append(class_info)

    # --- 模块级 body ✅ ---
    module_gen = None
    if module_stmts:
        try:
            module_ir = builder.build_module(ast.Module(body=module_stmts, type_ignores=[]))
            module_gen = codegen.FunctionGen(
            "<module>", None, [], Scope(name="<module>"), consts,
            package=module_name.rpartition(".")[0],
            lambda_names=lambda_names,
            mdef_names=mdef_names,
        )
            # **模块体是在这里就地生成的** ✅ -> 控制流里的类要先能查到源码 ✅
            module_gen.inline_class_sources = dict(result.inline_class_sources)
            for stmt in module_ir.get("body", []):
                module_gen.emit_stmt(stmt)
                # **每条语句结束时的行数** ✅ —— 类要按源码位置插进模块体 ✅
                module_gen.stmt_marks.append(len(module_gen.lines))
        except Unsupported as exc:
            result.module_ok = False
            result.module_reason = exc.reason
            return result

    if module_gen is not None:
        key_names.extend(sorted(module_gen.global_names))
        result.genexp_sources.update(module_gen.genexp_sources)
    result.body_marks = list(module_gen.stmt_marks) if module_gen is not None else []
    # **类信息总表** ✅（属性槽位快路径用 ✅）—— 必须在生成函数**之后**汇总 ✅，
    # 因为那时才知道每个类最终有哪些槽位 ✅；生成函数时先用**声明好的**表 ✅。
    result.key_names = key_names
    result.module_gen = module_gen
    result.decorated_functions = decorated_functs
    result.fallback_classes = fallback_classes
    result.class_order = class_order
    # **单阶段模块** ✅ —— init 直接返回**模块对象** ✅（PyModule_New ✅）
    # → stub 的 is_def 判定为假 ✅ → 直接返回模块 ✅ → 模块体已在 init 里执行 ✅
    # → meta.json 标 exec_done ✅ → 引导模块不再调 exec_def ✅（否则会重跑 ✅）。
    try:
        result.c_source = codegen.generate_module_single_phase(
        module_name,
        result.init_symbol,
        result.functions,
        key_names,
        module_gen,
        None,
        result.fallback_code,
        result.classes,
        consts,
        result.func_meta,
        result.genexp_sources,
        result.decorated_functions,
            source_file=result.source_path,
            fallback_classes=result.fallback_classes,
            class_order=result.class_order,
            body_marks=result.body_marks,
            inline_class_sources=result.inline_class_sources,
        class_factories=result.class_factories,
        )
    except Unsupported as exc:
        # **生成期的不支持** ✅（例如池里的常量类型 ✅）-> 模块级回退 ✅ 不能崩 ✅
        result.module_ok = False
        result.module_reason = "代码生成：" + exc.reason
        return result
    result.consts = consts
    return result

def build_pyd(module_compile: ModuleCompile, out_dir: Path, python_prefix: Path | None = None,
              alias_init: str | None = None, fingerprint: str | None = None,
              stamp_path: Path | None = None) -> Path:
    """把生成的 C 编译成 .pyd ✅（工具链由**平台接口**提供 ✅ —— M6 ✅）。

    产物文件名用 **init 符号的末段** ✅（cratix_<hash>.pyd ✅）——
    这样标准的 ExtensionFileLoader 也能按名字找到 PyInit ✅（开发期测试方便 ✅）；
    打包后由 cratix_loader 按 meta.json 里的 init 显式调用 ✅（不依赖文件名 ✅）。
    """
    python_prefix = Path(python_prefix or sys.base_prefix)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = module_compile.init_symbol[len("PyInit_"):]
    c_path = out_dir / (stem + ".c")
    # **扩展后缀由平台决定** ✅（Windows .pyd ✅ / Linux .so ✅）
    pyd_path = out_dir / (stem + platforms.current().extension_suffix)
    # **增量** ✅：指纹一致且产物在 ✅ -> 跳过编译 ✅（省掉 cl + link ✅）。
    if fingerprint and stamp_path is not None and pyd_path.is_file():
        try:
            if stamp_path.read_text(encoding="utf-8").strip() == fingerprint:
                return pyd_path
        except OSError:
            pass
    if alias_init:
        # **必须带上 fallback_code 与 classes** ✅ —— 漏传 ✅ 会丢掉类与回退函数 ✅
        module_compile.c_source = codegen.generate_module_single_phase(
            module_compile.module_name,
            module_compile.init_symbol,
            module_compile.functions,
            module_compile.key_names,
            module_compile.module_gen,
            alias_init=alias_init,
            fallback_code=module_compile.fallback_code,
            classes=module_compile.classes,
            consts=module_compile.consts,
            func_meta=module_compile.func_meta,
            genexps=module_compile.genexp_sources,
            decorated_functions=module_compile.decorated_functions,
            source_file=module_compile.source_path,
            fallback_classes=module_compile.fallback_classes,
            class_order=module_compile.class_order,
            body_marks=module_compile.body_marks,
        )
    c_path.write_text(module_compile.c_source, encoding="utf-8", newline="")
    (out_dir / HEADER_NAME).write_text(RUNTIME_H, encoding="utf-8", newline="")

    # **只认平台接口** ✅（M6 ✅）—— 工具链细节在 platforms/windows.py ✅
    plat = platforms.current()
    plat.build_extension(c_path, pyd_path, python_prefix)
    pyd = pyd_path
    if not pyd.is_file():
        raise RuntimeError("未生成 .pyd：" + str(pyd))
    if fingerprint and stamp_path is not None:
        try:
            stamp_path.write_text(fingerprint, encoding="utf-8")
        except OSError:
            pass
    return pyd