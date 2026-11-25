"""IR -> C 代码生成 ✅（C-API 后端 ✅）。

生成策略：**语句式** ✅ —— 每个表达式落成一个临时变量 ✅，
这样短路运算 ✅ / 条件表达式 ✅ / 推导式 ✅ 都能自然表达 ✅。

所有权纪律 ✅：
    - 临时变量与局部变量都持有**新引用** ✅；
    - 赋值是"转移所有权" ✅（旧值先 DECREF ✅，新值置 NULL 防二次释放 ✅）；
    - 函数出口统一清理 ✅（正常路径与错误路径汇合 ✅）。

v1 **不支持** try/except/finally 与 with ✅（需要 C 级异常展开 ✅ —— 留待 M3 ✅）；
这些函数整函数回退 .pyc ✅（见 compiler/fallback.py ✅）。
"""

from __future__ import annotations

import ast

from ..frontend.subset import Unsupported


# 向量调用的参数槽位上限 ✅（超出走通用路径 ✅ ✅）。
MAX_VECTORCALL_ARGS = 16

# try/except 的实现上限 ✅（嵌套深度 ✅ / 每层 handler 数 ✅）。
MAX_TRY_DEPTH = 8
MAX_TRY_HANDLERS = 4
# **with 帧的编号基址** ✅（>= 该值即 with 帧 ✅ —— 与 try 帧区分 ✅）。
WITH_FRAME_BASE = 1000
# **finally 帧的编号基址** ✅（再高一档 ✅）
FINALLY_FRAME_BASE = 2000
# finally 嵌套上限 ✅（与 try 共用同一个栈 ✅）
MAX_FINALLY_DEPTH = MAX_TRY_DEPTH


class _TryBody:
    """try 的"内层" ✅ —— 剥掉 finally 之后要执行的语句 ✅。

    有 handler -> 生成一个**没有 finally 的 try 节点** ✅（复用同一套 handler 分派 ✅）；
    没有 handler -> 直接是 body（+ else）✅（纯 try/finally 不压 try 帧 ✅）。
    """

    def __init__(self, node):
        self.body = list(node.get("body", []))
        self.handlers = list(node.get("handlers", []))
        self.orelse = list(node.get("orelse", []))

    def statements(self) -> list:
        if not self.handlers:
            return list(self.body) + list(self.orelse)
        from .. import ir as _ir

        return [
            _ir.node(
                "try",
                body=self.body,
                handlers=self.handlers,
                orelse=self.orelse,
                finalbody=[],
            )
        ]


def c_ident(name: str) -> str:
    """Python 名字 -> C 标识符 ✅（加前缀避开 C 关键字 ✅）。"""
    out = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    return "v_" + "".join(out)


def c_string(text: str) -> str:
    """字符串字面量 -> C 字符串 ✅（按 **UTF-8 字节**转义 ✅）。

    **用八进制转义** ✅（\\NNN ✅）而不是 \\xNN ✅ —— 后者后面紧跟十六进制字符时会歧义 ✅。
    """
    out = ["\""]
    for byte in text.encode("utf-8"):
        if byte == 0x5C:
            out.append("\\\\")
        elif byte == 0x22:
            out.append("\\" + chr(34))
        elif byte == 0x0A:
            out.append("\\n")
        elif byte == 0x0D:
            out.append("\\r")
        elif byte == 0x09:
            out.append("\\t")
        elif 0x20 <= byte <= 0x7E:
            out.append(chr(byte))
        else:
            out.append("\\%03o" % byte)
    out.append("\"")
    return "".join(out)


BINOP_FUNCS = {
    "+": "PyNumber_Add",
    "-": "PyNumber_Subtract",
    "*": "PyNumber_Multiply",
    "/": "PyNumber_TrueDivide",
    "//": "PyNumber_FloorDivide",
    "%": "PyNumber_Remainder",
    "**": "PyNumber_Power",
    "<<": "PyNumber_Lshift",
    ">>": "PyNumber_Rshift",
    "|": "PyNumber_Or",
    "^": "PyNumber_Xor",
    "&": "PyNumber_And",
}

AUG_FUNCS = {
    "+": "PyNumber_InPlaceAdd",
    "-": "PyNumber_InPlaceSubtract",
    "*": "PyNumber_InPlaceMultiply",
    "/": "PyNumber_InPlaceTrueDivide",
    "//": "PyNumber_InPlaceFloorDivide",
    "%": "PyNumber_InPlaceRemainder",
    "**": "PyNumber_InPlacePower",
    "<<": "PyNumber_InPlaceLshift",
    ">>": "PyNumber_InPlaceRshift",
    "|": "PyNumber_InPlaceOr",
    "^": "PyNumber_InPlaceXor",
    "&": "PyNumber_InPlaceAnd",
}

CMP_CODES = {
    "==": "Py_EQ",
    "!=": "Py_NE",
    "<": "Py_LT",
    "<=": "Py_LE",
    ">": "Py_GT",
    ">=": "Py_GE",
}


def const_expr(value) -> str:
    """常量 -> C 表达式 ✅（返回**新引用** ✅）。"""
    if value is None:
        return "Py_NewRef(Py_None)"
    if value is True:
        return "Py_NewRef(Py_True)"
    if value is False:
        return "Py_NewRef(Py_False)"
    if isinstance(value, int):
        return "PyLong_FromLongLong(%d)" % value
    if isinstance(value, float):
        return "PyFloat_FromDouble(%s)" % repr(value)
    if isinstance(value, str):
        return "PyUnicode_FromString(%s)" % c_string(value)
    if isinstance(value, bytes):
        return "PyBytes_FromStringAndSize(%s, %d)" % (c_bytes_literal(value), len(value))
    raise Unsupported("常量类型：" + type(value).__name__)


class _CellRewriter(ast.NodeTransformer):
    """把**捕获的名字**改写成 `_cratix_cells["名字"]` ✅。

    **放在模块级** ✅ —— 以前写在函数里（嵌套 class ✅）会让那个函数整函数回退 ✅。
    """

    def __init__(self, captured):
        self.captured = set(captured)

    def visit_Name(self, node):
        if node.id in self.captured and isinstance(node.ctx, ast.Load):
            return ast.Subscript(
                value=ast.Name(id="_cratix_cells", ctx=ast.Load()),
                slice=ast.Constant(value=node.id),
                ctx=ast.Load(),
            )
        return node


def _rewrite_cells(expr_src: str, captured) -> str:
    """把**捕获的名字**改写成 `_cratix_cells["名字"]` ✅（保语义 ✅ 晚绑定 ✅）。"""
    tree = ast.parse(expr_src, mode="eval")
    rewritten = _CellRewriter(captured).visit(tree)
    return ast.unparse(ast.fix_missing_locations(rewritten))


def genexp_source(name: str, elt_src: str, gen_texts, captured=None) -> str:
    """合成**生成器函数**源码 ✅ —— 最外层可迭代对象由参数传入 ✅（立即求值 ✅）。"""
    nl = chr(10)
    captured = set(captured or ())
    if captured:
        elt_src = _rewrite_cells(elt_src, captured)
        gen_texts = [
            (target, _rewrite_cells(iter_src, captured), [_rewrite_cells(c, captured) for c in ifs])
            for target, iter_src, ifs in gen_texts
        ]
        lines = ["def " + name + "(_cratix_cells, _cratix_it):"]
    else:
        lines = ["def " + name + "(_cratix_it):"]
    depth = 1
    for index, (target_src, iter_src, ifs) in enumerate(gen_texts):
        iterable = "_cratix_it" if index == 0 else "(" + iter_src + ")"
        lines.append("    " * depth + "for " + target_src + " in " + iterable + ":")
        depth += 1
        for cond in ifs:
            lines.append("    " * depth + "if not (" + cond + "):")
            lines.append("    " * (depth + 1) + "continue")
    lines.append("    " * depth + "yield (" + elt_src + ")")
    return nl.join(lines) + nl


def trampoline_source(tb_name: str, private: str, params) -> str:
    """生成**跳板函数**源码 ✅ —— 能给出真实签名时就用真实签名 ✅。

    为什么值得 ✅：`inspect.signature(instance.method)` 会读跳板的 code object ✅ ——
    泛型跳板（`*args, **kwargs`）会让签名变成 `(*args, **kwargs)` ✅（与源码不一致 ✅）。
    条件（不满足就退回泛型 ✅，**语义永远正确优先** ✅）：
      - 没有 `*args` / `**kwargs` / 仅关键字参数 ✅；
      - 所有默认值都是**字面量** ✅（可以直接写进源码 ✅）。
    """
    names = list(params.get("names", []))
    defaults = list(params.get("defaults", []))
    if params.get("vararg") or params.get("kwarg") or params.get("kwonly"):
        return (
            "def " + tb_name + "(self, *args, **kwargs):" + chr(10)
            + "    return " + private + "(self, *args, **kwargs)" + chr(10)
        )
    parts = []
    for index, name in enumerate(names):
        # **builder 把"没有默认值"填成 None** ✅（defaults 与 names 等长 ✅）
        node = defaults[index] if index < len(defaults) else None
        if node is None:
            parts.append(name)
            continue
        if node.kind != "const":
            return (
                "def " + tb_name + "(self, *args, **kwargs):" + chr(10)
                + "    return " + private + "(self, *args, **kwargs)" + chr(10)
            )
        parts.append(name + "=" + repr(node.get("value")))
    # 关键字参数在这里会被绑到跳板的同名参数上 ✅ -> 再按位置传下去 ✅（等价 ✅）
    forwarded = ", ".join(names)
    return (
        "def " + tb_name + "(" + ", ".join(parts) + "):" + chr(10)
        + "    return " + private + "(" + forwarded + ")" + chr(10)
    )


def method_doc(name: str, meta) -> str:
    """CPython 的 ml_doc 约定 ✅：文本签名 + 分隔符 + 文档 ✅。"""
    if not meta:
        return ""
    doc = meta.get("doc") or ""
    sig = meta.get("sig") or ""
    if sig:
        # **名字前缀是硬要求** ✅：CPython 先比对 doc[:len(name)] == name 且紧跟 "(" ✅
        # 少了名字 -> 解析失败 ✅ -> __text_signature__ 为 None 且 __doc__ 变成整串 ✅（实测 ✅）
        # 分隔符是 "\n--\n\n" ✅ -> __text_signature__ 与 __doc__ 各归各位 ✅
        return name + sig + chr(10) + "--" + chr(10) + chr(10) + doc
    return doc


class ConstantPool:
    """**模块级常量池** ✅ —— 每个字面量只创建一次 ✅。

    不建池的话 ✅：循环里每轮都 `PyLong_FromLongLong(2)` / `PyUnicode_FromString(...)` ✅ ——
    纯开销 ✅（CPython 解释器是直接把常量从 co_consts 里 LOAD_CONST ✅）。
    池化之后 ✅：常量在模块初始化时建一次 ✅，用的时候只 `Py_INCREF` ✅。
    """

    def __init__(self):
        self.entries = []          # [(C 变量名, 值)]
        self.index = {}            # (类型名, 值) -> C 变量名

    def ref(self, value) -> str:
        # **类型进键** ✅ —— True / 1 / 1.0 是三个不同的常量 ✅
        key = (type(value).__name__, value)
        name = self.index.get(key)
        if name is not None:
            return name
        name = "cratix_c" + str(len(self.entries))
        self.index[key] = name
        self.entries.append((name, value))
        return name

    def adopt(self, other) -> None:
        """把另一个池的常量并进来 ✅（多个生成器共用一个模块 ✅）。"""
        for _name, value in getattr(other, "entries", []):
            self.ref(value)


def emit_pool_statics(out: list, pool) -> None:
    """常量池的**静态声明** ✅（翻译单元级 ✅ —— 所有函数共享 ✅）。

    **插到 `#include` 之后** ✅ —— 声明必须在文件作用域 ✅，
    而且类型 `PyObject` 要先由 `Python.h` 声明 ✅（插在开头会 C2143 ✅ 实测 ✅）。
    """
    if pool is None or not pool.entries:
        return
    decls = [""]
    for name, _value in pool.entries:
        decls.append("static PyObject *" + name + " = NULL;")
    index = 0
    for i, line in enumerate(out):
        if line.startswith("#include"):
            index = i + 1
    out[index:index] = decls


def emit_pool_init(out: list, pool, indent: str = "    ") -> None:
    """常量池的**初始化** ✅（模块 init 里执行一次 ✅）。"""
    if pool is None or not pool.entries:
        return
    for name, value in pool.entries:
        out.append(indent + name + " = " + const_expr(value) + ";")
        out.append(indent + "if (" + name + " == NULL) return -1;")


def generate_function(gen: FunctionGen) -> str:
    """生成一个函数的完整 C 代码 ✅。"""
    nl = chr(10)
    body = nl.join(gen.lines)
    decls = []
    for name in sorted(gen.scope.locals_):
        decls.append("    PyObject *" + c_ident(name) + " = NULL;")
    # **每个临时变量都要单独初始化** ✅ ——
    # 写成 `PyObject *a, *b = NULL;` ✅ 只有最后一个被初始化 ✅ 其余是**野指针** ✅
    # → 清理时 Py_XDECREF(野指针 ✅) → **访问违例** ✅（实测 ✅）。
    for name in gen.tmp_names:
        decls.append("    PyObject *" + name + " = NULL;")
    for name in gen.helper_names:
        decls.append("    PyObject *" + name + " = NULL;")
    for name in gen.int_helpers:
        decls.append("    int " + name + " = 0;")
    # **向量调用的暂存** ✅（每个函数一份 ✅ —— 避免重复声明 ✅ ✅）
    decls.append("    PyObject *cratix_vargs[" + str(MAX_VECTORCALL_ARGS + 1) + "];")
    decls.append("    PyObject *cratix_kwnames = NULL;")
    decls.append("    PyObject *cratix_mname = NULL;")
    # **try 栈** ✅（每个函数一份 ✅ —— try/except 的实现状态 ✅ ✅）
    decls.append("    int cratix_try_n = 0;")
    decls.append("    int cratix_try_idx[" + str(MAX_TRY_DEPTH) + "];")
    decls.append("    PyObject *cratix_try_types[" + str(MAX_TRY_DEPTH) + "][" + str(MAX_TRY_HANDLERS) + "];")
    decls.append("    int cratix_try_nh[" + str(MAX_TRY_DEPTH) + "];")
    decls.append("    PyObject *cratix_with_mgr[" + str(MAX_TRY_DEPTH) + "] = { NULL };")
    # **interned 属性名** ✅ —— 必须声明在函数顶部 ✅（块内声明在别的分支不可见 ✅ ✅）
    for ref in sorted(gen.declared_attrs):
        decls.append("    static PyObject *" + ref + " = NULL;")
    cleanup = []
    # **cratix_kwv 是借用引用** ✅（PyDict_GetItemWithError ✅）—— **不能 DECREF** ✅
    # （多 DECREF 一次 ✅ → 提前释放 ✅ → 后续 use-after-free ✅ → 访问违例 ✅）
    if getattr(gen, "method_mode", False):
        cleanup.append("    Py_XDECREF(cratix_shifted);")
    cleanup.append("    Py_XDECREF(cratix_kw);")
    for name in sorted(gen.scope.locals_):
        cleanup.append("    Py_XDECREF(" + c_ident(name) + ");")
    for name in gen.helper_names:
        cleanup.append("    Py_XDECREF(" + name + ");")
    for name in gen.tmp_names:
        cleanup.append("    Py_XDECREF(" + name + ");")
    lines = []
    lines.append("static PyObject *cratix_fn_" + c_ident(gen.func_name) + "(PyObject *self, PyObject *args, PyObject *kwargs) {")
    lines.append("    PyObject *result = NULL;")
    lines.extend(decls)
    lines.append(body)
    lines.append("    result = Py_NewRef(Py_None);")
    lines.append("    goto cratix_unwind;")
    lines.append("cratix_error:")
    lines.extend(gen.emit_try_handlers())
    lines.append("    result = NULL;")
    lines.append("    goto cratix_done;")
    lines.append("cratix_unwind:")
    lines.extend(gen.emit_unwind_code())
    lines.append("cratix_done:")
    lines.extend(cleanup)
    lines.append("    return result;")
    lines.append("}")
    return nl.join(lines)

def module_body_parts(gen: FunctionGen):
    """模块级语句 -> (声明, 语句, 清理) ✅。"""
    nl = chr(10)
    decls = []
    for name in gen.tmp_names:
        decls.append("    PyObject *" + name + " = NULL;")
    for name in gen.helper_names:
        decls.append("    PyObject *" + name + " = NULL;")
    for name in gen.int_helpers:
        decls.append("    int " + name + " = 0;")
    # **向量调用的暂存** ✅（每个函数一份 ✅ —— 避免重复声明 ✅ ✅）
    decls.append("    PyObject *cratix_vargs[" + str(MAX_VECTORCALL_ARGS + 1) + "];")
    decls.append("    PyObject *cratix_kwnames = NULL;")
    decls.append("    PyObject *cratix_mname = NULL;")
    # **try 栈** ✅（每个函数一份 ✅ —— try/except 的实现状态 ✅ ✅）
    decls.append("    int cratix_try_n = 0;")
    decls.append("    int cratix_try_idx[" + str(MAX_TRY_DEPTH) + "];")
    decls.append("    PyObject *cratix_try_types[" + str(MAX_TRY_DEPTH) + "][" + str(MAX_TRY_HANDLERS) + "];")
    decls.append("    int cratix_try_nh[" + str(MAX_TRY_DEPTH) + "];")
    decls.append("    PyObject *cratix_with_mgr[" + str(MAX_TRY_DEPTH) + "] = { NULL };")
    for ref in sorted(gen.declared_attrs):
        decls.append("    static PyObject *" + ref + " = NULL;")
    cleanup = []
    for name in gen.helper_names:
        cleanup.append("    Py_XDECREF(" + name + ");")
    for name in gen.tmp_names:
        cleanup.append("    Py_XDECREF(" + name + ");")
    return decls, list(gen.lines), cleanup




def generate_module_single_phase(module_name: str, init_symbol: str, functions: list,
                                 key_names: list, module_gen=None, alias_init: str | None = None,
                                 fallback_code: dict | None = None, classes: list | None = None,
                                 consts=None, func_meta=None, genexps=None,
                                 decorated_functions=None, source_file="") -> str:
    """生成整个模块的 C 代码 ✅ —— **单阶段初始化** ✅（与 Cython 一致 ✅）。

    **为什么单阶段** ✅：
    - CPython 的 `PyModuleDef_Init()` 返回**模块对象** ✅（不是 def 指针 ✅）——
      stub 的多阶段判定 `mod->ob_type == PyModuleDef_Type` ✅ 对它不成立 ✅；
    - 单阶段下 init 直接返回模块 ✅ → stub 的单阶段路径 ✅ → 模块体在 init 里已执行 ✅；
    - 代价：模块体**只能执行一次** ✅ → bootstrap **不要**再调 `exec_def` ✅。
    """
    nl = chr(10)
    q = chr(34)
    out = []
    out.append("/* 由 cratix AOT 编译器生成 —— 请勿手改 ✅ */")
    out.append("#include <Python.h>")
    out.append("#include " + q + "cratix_runtime.h" + q)
    pass


    out.append("")
    out.append("static PyObject *cratix_globals = NULL;")
    out.append("static PyObject *cratix_builtins = NULL;")
    out.append("static PyObject *cratix_builtins_module = NULL;")
    out.append("")
    for key in sorted(set(key_names)):
        out.append("static PyObject *cratix_key_" + c_ident(key) + " = NULL;")
    out.append("")
    # **代码生成覆盖所有函数** ✅（含类方法 ✅ —— 它们的 C 代码要有 ✅）
    all_gens = list(functions)
    for cls in (classes or []):
        for _method_name, gen in cls.get("methods", []):
            all_gens.append(gen)
    for gen in all_gens:
        out.append(generate_function(gen))
        out.append("")
    out.append("static PyMethodDef cratix_methods[] = {")
    for gen in functions:
        cast = "(PyCFunction)(void(*)(void))cratix_fn_" + c_ident(gen.func_name)
        # **元数据保真** ✅：ml_doc 带文本签名 + 文档 ✅（inspect.signature 可用 ✅）
        _doc = method_doc(gen.func_name, (func_meta or {}).get(gen.func_name))
        out.append("    {" + q + gen.func_name + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, " + c_string(_doc) + "},")
    out.append("    {NULL, NULL, 0, NULL}")
    out.append("};")
    out.append("")
    # --- 模块体 ✅（在 init 里执行一次 ✅）---
    out.append("static int cratix_run_body(PyObject *m) {")
    # **常量池初始化** ✅（模块 init 里只做一次 ✅ —— 函数与模块体都要用 ✅）
    emit_pool_init(out, consts)
    body_decls = []
    body_cleanup = []
    if module_gen is not None:
        body_decls, body_lines, body_cleanup = module_body_parts(module_gen)
        out.extend(body_decls)
    out.append("    cratix_globals = PyModule_GetDict(m);")
    out.append("    if (cratix_globals == NULL) return -1;")
    out.append("    cratix_builtins_module = PyImport_AddModule(\"builtins\");")
    out.append("    if (cratix_builtins_module == NULL) return -1;")
    out.append("    cratix_builtins = PyModule_GetDict(cratix_builtins_module);")
    out.append("    if (cratix_builtins == NULL) return -1;")
    # **模块字典要有 __builtins__** ✅ —— exec/eval 源码时（回退函数 ✅/跳板 ✅/lambda ✅）
    # CPython 会去 globals 找它 ✅；没有就往上找帧 ✅ -> 没有帧时 `KeyError: '__builtins__'` ✅（实测 ✅）
    out.append("    if (PyDict_SetItemString(cratix_globals, \"__builtins__\", cratix_builtins) < 0) return -1;")
    if source_file:
        # **`__file__` 保真** ✅ —— 源码路径在编译期就知道 ✅（运行期不去猜 ✅）
        out.append("    {")
        out.append("        PyObject *cratix_file = PyUnicode_FromString(" + c_string(source_file) + ");")
        out.append("        if (cratix_file == NULL) return -1;")
        out.append("        if (PyDict_SetItemString(cratix_globals, \"__file__\", cratix_file) < 0) { Py_DECREF(cratix_file); return -1; }")
        out.append("        Py_DECREF(cratix_file);")
        out.append("    }")
    for key in sorted(set(key_names)):
        out.append("    cratix_key_" + c_ident(key) + " = PyUnicode_InternFromString(" + q + key + q + ");")
        out.append("    if (cratix_key_" + c_ident(key) + " == NULL) return -1;")
    # **生成器表达式的辅助函数** ✅ —— 必须在 `cratix_globals` 就绪**之后** ✅、
    # 模块体与类创建**之前** ✅（装饰器/模块体都可能用到 ✅）；
    # 放在 globals 之前实测报 "globals must be a real dict" ✅
    for gen_name in sorted(genexps or {}):
        out.append("    {")
        out.append("        PyObject *cratix_g = PyRun_String(" + c_string(genexps[gen_name]) + ", Py_file_input, cratix_globals, cratix_globals);")
        out.append("        if (cratix_g == NULL) return -1;")
        out.append("        Py_DECREF(cratix_g);")
        out.append("    }")
    # **模块体先跑** ✅ —— 装饰器名往往来自模块体的 import ✅（实测：先套装饰器会 NameError ✅）
    if module_gen is not None:
        out.extend(body_lines)
    # **模块级装饰器** ✅ —— 原生实现已经是模块属性 ✅；造一个 **Python 跳板** ✅
    # （把原生函数绑成私有名 ✅ 避免自引用递归 ✅），套上装饰器 ✅ 再换回公开名 ✅。
    for func_name, decorator_sources in (decorated_functions or []):
        raw_name = "_cratix_raw_" + func_name
        tb_name = "_cratix_dtb_" + func_name
        out.append("    {")
        out.append("        PyObject *cratix_raw = PyDict_GetItemString(cratix_globals, " + c_string(func_name) + ");")
        out.append("        if (cratix_raw == NULL) {")
        out.append("            PyErr_SetString(PyExc_NameError, \"cratix: decorated function missing\");")
        out.append("            return -1;")
        out.append("        }")
        out.append("        Py_INCREF(cratix_raw);")
        out.append("        if (PyDict_SetItemString(cratix_globals, " + c_string(raw_name) + ", cratix_raw) < 0) { Py_DECREF(cratix_raw); return -1; }")
        out.append("        Py_DECREF(cratix_raw);")
        tb_source = trampoline_source(tb_name, raw_name, {"names": [], "vararg": "cratix_args", "kwarg": "cratix_kw"})
        out.append("        PyObject *cratix_tb = PyRun_String(" + c_string(tb_source) + ", Py_file_input, cratix_globals, cratix_globals);")
        out.append("        if (cratix_tb == NULL) return -1;")
        out.append("        Py_DECREF(cratix_tb);")
        out.append("        PyObject *cratix_fn = PyDict_GetItemString(cratix_globals, " + c_string(tb_name) + ");")
        out.append("        if (cratix_fn == NULL) return -1;")
        out.append("        Py_INCREF(cratix_fn);")
        for segment in reversed([s for s in decorator_sources if s]):
            out.append("        {")
            out.append("            PyObject *dec = PyRun_String(" + c_string(segment) + ", Py_eval_input, cratix_globals, cratix_globals);")
            out.append("            if (dec == NULL) { Py_DECREF(cratix_fn); return -1; }")
            out.append("            PyObject *wrapped = PyObject_CallOneArg(dec, cratix_fn);")
            out.append("            Py_DECREF(dec);")
            out.append("            if (wrapped == NULL) { Py_DECREF(cratix_fn); return -1; }")
            out.append("            Py_DECREF(cratix_fn);")
            out.append("            cratix_fn = wrapped;")
            out.append("        }")
        out.append("        if (PyDict_SetItemString(cratix_globals, " + c_string(func_name) + ", cratix_fn) < 0) { Py_DECREF(cratix_fn); return -1; }")
        out.append("        Py_DECREF(cratix_fn);")
        out.append("    }")
    if module_gen is not None:
        out.extend(body_cleanup)
    out.append("    return 0;")
    if module_gen is not None:
        out.append("cratix_error:")
        out.extend(body_cleanup)
        out.append("    return -1;")
    out.append("}")
    out.append("")
    # --- ��� ✅：每类一个方法表 ✅（方法名带类名前缀 ✅ —— 避免同名冲突 ✅）---
    classes = classes or []
    for cls in classes:
        descriptors = set(cls.get("descriptor_methods") or ())
        # **`__init__` 有原生实现 -> 走 `tp_init`** ✅：`type_call` 会**直接**调 ✅，
        # 省掉"查 __init__ 属性 + 造绑定方法"✅（造实例密集时差别很大 ✅）。
        has_native_init = "__init__" in descriptors
        if has_native_init:
            out.append("static int cratix_init_" + c_ident(cls["name"]) + "(PyObject *self, PyObject *args, PyObject *kwargs) {")
            out.append("    PyObject *res = cratix_fn_" + c_ident(cls["name"] + ".__init__") + "(self, args, kwargs);")
            out.append("    if (res == NULL) return -1;")
            out.append("    Py_DECREF(res);")
            out.append("    return 0;")
            out.append("}")
            out.append("")
        # **tp_new** ✅：分配实例 ✅（初始化交给 tp_init ✅；没有原生 __init__ 时才自己调 ✅）
        out.append("static PyObject *cratix_new_" + c_ident(cls["name"]) + "(PyTypeObject *type, PyObject *args, PyObject *kwargs) {")
        out.append("    PyObject *self = type->tp_alloc(type, 0);")
        out.append("    PyObject *init; PyObject *res;")
        out.append("    if (self == NULL) return NULL;")
        if has_native_init:
            out.append("    (void)init; (void)res; (void)args; (void)kwargs;")
            out.append("    return self;")
        else:
            out.append("    init = PyObject_GetAttrString(self, \"__init__\");")
            out.append("    if (init == NULL) { Py_DECREF(self); return NULL; }")
            out.append("    res = PyObject_Call(init, args, kwargs);")
            out.append("    Py_DECREF(init);")
            out.append("    if (res == NULL) { Py_DECREF(self); return NULL; }")
            out.append("    Py_DECREF(res);")
            out.append("    return self;")
        out.append("}")
        out.append("")
        # **实例结构体 = 属性槽位** ✅（M5 ✅）—— 属性存在 C 结构里 ✅，
        # 编译产物读 `self.x` 时可以**按偏移直接取** ✅（不必过属性协议 ✅）。
        # 仍然保留 MANAGED_DICT ✅ -> 没声明过的属性照旧可以挂在实例字典上 ✅。
        slots = list(cls.get("slots") or ())
        struct_name = "cratix_inst_" + c_ident(cls["name"])
        out.append("static PyMemberDef cratix_cls_" + c_ident(cls["name"]) + "_members[] = {")
        for index, slot_name in enumerate(slots):
            # **Py_T_OBJECT_EX** ✅：3.12+ 的公开名字 ✅（`structmember.h` 里的 T_OBJECT_EX 已废弃 ✅）
            out.append("    {" + q + slot_name + q + ", Py_T_OBJECT_EX, offsetof(" + struct_name + ", s" + str(index) + "), 0, NULL},")
        out.append("    {NULL, 0, 0, 0, NULL}")
        out.append("};")
        out.append("")
        # **GC 回调** ✅ —— HAVE_GC 要求 traverse ✅；
        # 托管字典也要 visit/clear ✅（否则字典里的循环引用回收不到 ✅）。
        out.append("static int cratix_traverse_" + c_ident(cls["name"]) + "(PyObject *self, visitproc visit, void *arg) {")
        out.append("    Py_VISIT(Py_TYPE(self));")
        out.append("    PyObject_VisitManagedDict(self, visit, arg);")
        for index, _slot in enumerate(slots):
            out.append("    Py_VISIT(((" + struct_name + " *)self)->s" + str(index) + ");")
        out.append("    return 0;")
        out.append("}")
        out.append("")
        out.append("static int cratix_clear_" + c_ident(cls["name"]) + "(PyObject *self) {")
        out.append("    PyObject_ClearManagedDict(self);")
        for index, _slot in enumerate(slots):
            out.append("    Py_CLEAR(((" + struct_name + " *)self)->s" + str(index) + ");")
        out.append("    return 0;")
        out.append("}")
        out.append("")
        out.append("static PyType_Slot cratix_cls_" + c_ident(cls["name"]) + "_slots[] = {")
        out.append("    {Py_tp_new, (void *)cratix_new_" + c_ident(cls["name"]) + "},")
        out.append("    {Py_tp_members, (void *)cratix_cls_" + c_ident(cls["name"]) + "_members},")
        if has_native_init:
            out.append("    {Py_tp_init, (void *)cratix_init_" + c_ident(cls["name"]) + "},")
        out.append("    {Py_tp_traverse, (void *)cratix_traverse_" + c_ident(cls["name"]) + "},")
        out.append("    {Py_tp_clear, (void *)cratix_clear_" + c_ident(cls["name"]) + "},")
        out.append("    {0, NULL}")
        out.append("};")
        out.append("")
        out.append("static PyType_Spec cratix_cls_" + c_ident(cls["name"]) + "_spec = {")
        out.append("    " + q + module_name + "." + cls["name"] + q + ",")
        out.append("    sizeof(" + struct_name + "), 0,")
        # **MANAGED_DICT 必须配 HAVE_GC** ✅ —— 托管字典的指针存在对象**前置头**里 ✅，
        # 没有 GC 标志就不会预留那块空间 ✅ -> 成员偏移与托管字典**重叠** ✅ -> 堆损坏 ✅（实测 ✅）。
        out.append("    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_MANAGED_DICT,")
        out.append("    cratix_cls_" + c_ident(cls["name"]) + "_slots")
        out.append("};")
        out.append("")
        descriptors = set(cls.get("descriptor_methods") or ())
        out.append("static PyMethodDef cratix_cls_" + c_ident(cls["name"]) + "_methods[] = {")
        for method_name, _gen in cls.get("methods", []):
            if method_name in descriptors:
                continue
            cast = "(PyCFunction)(void(*)(void))cratix_fn_" + c_ident(cls["name"] + "." + method_name)
            # **私有名带类名** ✅ —— 不同类的同名方法（如 __init__ ✅）不会冲突 ✅
            private = "_cratix_" + cls["name"] + "_" + method_name
            out.append("    {" + q + private + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, NULL},")
        out.append("    {NULL, NULL, 0, NULL}")
        out.append("};")
        out.append("")
        # **原生方法描述符表** ✅（公开方法名 ✅ —— 描述符按这个方法名建 ✅）
        if descriptors:
            out.append("static PyMethodDef cratix_cls_" + c_ident(cls["name"]) + "_desc[] = {")
            for method_name, gen in cls.get("methods", []):
                if method_name not in descriptors:
                    continue
                cast = "(PyCFunction)(void(*)(void))cratix_fn_" + c_ident(cls["name"] + "." + method_name)
                doc = method_doc(method_name, (func_meta or {}).get(cls["name"] + "." + method_name))
                out.append("    {" + q + method_name + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, " + c_string(doc) + "},")
            out.append("    {NULL, NULL, 0, NULL}")
            out.append("};")
            out.append("")
        # **跳板源码** ✅：把 self 传给原生方法 ✅（普通 PyCFunction ✅ 不绑定 self ✅）
        # **无装饰器的方法不再要跳板** ✅ —— 它们直接以**原生方法描述符**挂到类上 ✅（省一个 Python 帧 ✅）
        for method_name, _gen in cls.get("methods", []):
            if method_name in descriptors:
                continue
            tb_name = "_cratix_tb_" + cls["name"] + "_" + method_name
            private = "_cratix_" + cls["name"] + "_" + method_name
            src = trampoline_source(tb_name, private, _gen.params)
            out.append("static const char *cratix_tb_" + c_ident(cls["name"] + "." + method_name) + " = " + c_string(src) + ";")
        out.append("")
    out.append("static struct PyModuleDef cratix_module = {")
    out.append("    PyModuleDef_HEAD_INIT,")
    out.append("    " + q + module_name + q + ",")
    out.append("    NULL,")
    # **m_size = -1** ✅ —— `PyModule_ExecDef()` 要求 **m_size < 0** ✅
    # （m_size >= 0 是"带状态"模块 ✅ → ExecDef 报 "module has state" ✅）。
    out.append("    -1,")
    out.append("    cratix_methods,")
    out.append("    NULL,")
    out.append("    NULL, NULL, NULL")
    out.append("};")
    out.append("")
    # --- 函数级回退 ✅：内嵌 marshal 后的 code object ✅ ---
    # --- 函数级回退 ✅：内嵌**函数源码** ✅ ---
    # 运行期用 `Py_CompileString()` 编译 ✅（公开稳定 API ✅ —— 避开 marshal 的 C API ✅）。
    fallback_code = fallback_code or {}
    if fallback_code:
        out.append("typedef struct {")
        out.append("    const char *name;")
        out.append("    const char *source;")
        out.append("    const char *filename;")
        out.append("} cratix_fb_entry;")
        out.append("")
        out.append("static const cratix_fb_entry cratix_fallbacks[] = {")
        # **按源码顺序安装** ✅ —— 装饰器在使用前必须已定义 ✅（按名字排会 NameError ✅）
        for name in sorted(fallback_code, key=lambda n: (fallback_code[n].get("order", 0), n)):
            item = fallback_code[name]
            out.append(
                "    {" + q + name + q + ", "
                + c_string(item.get("source", ""))
                + ", "
                + c_string(item.get("filename", "<cratix>"))
                + "},",
            )
        out.append("    {NULL, NULL, NULL}")
        out.append("};")
        out.append("")
    out.append("PyMODINIT_FUNC " + init_symbol + "(void) {")
    # **PyModule_New + AddFunctions + SetModuleDef** ✅ ——
    # PyModuleDef_Init 在 3.14 返回 **def 实例** ✅（不是模块 ✅）；
    # PyModule_Create 在 3.14 要求 **m_size >= 0** ✅（我们是 -1 ✅ → 报
    # "bad argument to internal function" ✅）。
    # → 手工搭模块 ✅：名字 ✅ + 方法表 ✅ + def 关联 ✅（供 GetDef ✅）。
    out.append("    PyObject *m = PyModule_New(" + q + module_name + q + ");")
    out.append("    if (m == NULL) return NULL;")
    out.append("    if (PyModule_AddFunctions(m, cratix_methods) < 0) { Py_DECREF(m); return NULL; }")
    # **不设 m_def** ✅ —— `PyModule_SetModuleDef` 在 3.14 **没有导出** ✅（实测 ✅）；
    # 我们也不需要 ✅ —— 引导模块对 exec_done 的记录**不会**调 exec_def ✅。
    out.append("    if (cratix_run_body(m) < 0) { Py_DECREF(m); return NULL; }")
    # --- 函数级回退 ✅：内嵌源码 -> 在模块字典里 exec ✅ ---
    # **必须排在类创建之前** ✅ —— 类装饰器/方法装饰器是在建类时就要求值的 ✅，
    # 装饰器本身可能正是回退函数 ✅（实测：顺序反了会 NameError ✅）。
    if fallback_code:
        out.append("    for (int i = 0; cratix_fallbacks[i].name != NULL; i++) {")
        # **在模块字典里 exec** ✅ —— 函数的 __globals__ 就是模块字典 ✅
        out.append("        PyObject *code = Py_CompileString(cratix_fallbacks[i].source, cratix_fallbacks[i].filename, Py_file_input);")
        out.append("        if (code == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        PyObject *res = PyEval_EvalCode(code, cratix_globals, cratix_globals);")
        out.append("        Py_DECREF(code);")
        out.append("        if (res == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        Py_DECREF(res);")
        out.append("    }")
    # --- 类 ✅：用堆类型创建 ✅（type(name, (object,), ns) ✅）---
    for cls in classes:
        # **每个类自己的描述符集合** ✅（循环不隔离作用域 ✅ —— 复用上一个类的集合会漏跳过 ✅）
        descriptors = set(cls.get("descriptor_methods") or ())
        table = "cratix_cls_" + c_ident(cls["name"]) + "_methods"
        out.append("    {")
        pass
        pass

        # 方法**在建类之后**挂上去 ✅ —— 3.14 的
        # `PyDescr_NewMethod(PyTypeObject *, PyMethodDef *)` ✅ 需要**类** ✅。
        # 用**方法描述符** ✅ → 调用时绑定 self ✅（普通 PyCFunction ✅ 不绑定 ✅）。
        pass


        # **只给"跳板方法"建私有 PyCFunction** ✅ —— 表里也只有它们 ✅（索引必须对齐 ✅）
        trampoline_methods = [
            (name, gen) for name, gen in cls.get("methods", []) if name not in descriptors
        ]
        for idx, (method_name, _gen) in enumerate(trampoline_methods):
            out.append("        {")
            out.append("            PyObject *fn = PyCFunction_NewEx(&" + table + "[" + str(idx) + "], NULL, NULL);")
            out.append("            if (fn == NULL) { Py_DECREF(m); return NULL; }")
            out.append("            if (PyDict_SetItemString(cratix_globals, " + q + "_cratix_" + cls["name"] + "_" + method_name + q + ", fn) < 0) { Py_DECREF(fn); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(fn);")
            out.append("        }")
        for method_name, _gen in cls.get("methods", []):
            if method_name in descriptors:
                continue
            tb = "_cratix_tb_" + cls["name"] + "_" + method_name
            out.append("        {")
            out.append("            PyObject *t = PyRun_String(cratix_tb_" + c_ident(cls["name"] + "." + method_name) + ", Py_file_input, cratix_globals, cratix_globals);")
            out.append("            if (t == NULL) { Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(t);")
            out.append("        }")
        # **建类** ✅ —— `PyType_FromSpec` ✅（要有**实例结构体**才能做属性槽位 ✅）。
        # 类属性/文档/跳板在**建类之后**用 SetAttr 挂 ✅。
        out.append("        PyObject *cls = (PyObject *)PyType_FromSpec(&cratix_cls_" + c_ident(cls["name"]) + "_spec);")
        out.append("        if (cls == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        cratix_type_" + c_ident(cls["name"]) + " = cls;")
        out.append("        Py_INCREF(cratix_type_" + c_ident(cls["name"]) + ");")
        out.append("        {")
        out.append("            PyObject *mod_name = PyUnicode_FromString(" + q + module_name + q + ");")
        out.append("            if (mod_name == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
        out.append("            if (PyObject_SetAttrString(cls, \"__module__\", mod_name) < 0) { Py_DECREF(mod_name); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
        out.append("            Py_DECREF(mod_name);")
        out.append("        }")
        # **类文档保真** ✅
        if cls.get("doc"):
            out.append("        {")
            out.append("            PyObject *cd = PyUnicode_FromString(" + c_string(cls["doc"]) + ");")
            out.append("            if (cd == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            if (PyObject_SetAttrString(cls, \"__doc__\", cd) < 0) { Py_DECREF(cd); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(cd);")
            out.append("        }")
        # **跳板进命名空间** ✅（普通 Python 函数 ✅ → 作为方法时自动绑定 self ✅ ✅）
        for method_name, _gen in cls.get("methods", []):
            if method_name in descriptors:
                continue
            tb = "_cratix_tb_" + cls["name"] + "_" + method_name
            decorators = (cls.get("method_decorators") or {}).get(method_name) or []
            out.append("        {")
            out.append("            PyObject *tbv = PyDict_GetItemString(cratix_globals, " + q + tb + q + ");")
            out.append("            if (tbv == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            Py_INCREF(tbv);")
            out.append("            PyObject *mn = PyUnicode_FromString(" + q + method_name + q + ");")
            out.append("            if (mn == NULL) { Py_DECREF(tbv); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            # **先设 __name__ 再套装饰器** ✅ —— property 等描述符没有可写的 __name__ ✅
            out.append("            if (PyObject_SetAttrString(tbv, \"__name__\", mn) < 0) { PyErr_Clear(); }")
            out.append("            Py_DECREF(mn);")
            # **方法元数据保真** ✅：__doc__ / __qualname__ / __module__ ✅
            _mmeta = (func_meta or {}).get(cls["name"] + "." + method_name) or {}
            if _mmeta.get("doc"):
                out.append("            {")
                out.append("                PyObject *dd = PyUnicode_FromString(" + c_string(_mmeta["doc"]) + ");")
                out.append("                if (dd != NULL) { PyObject_SetAttrString(tbv, \"__doc__\", dd); Py_DECREF(dd); }")
                out.append("            }")
            if _mmeta.get("qualname"):
                out.append("            {")
                out.append("                PyObject *qn = PyUnicode_FromString(" + c_string(_mmeta["qualname"]) + ");")
                out.append("                if (qn != NULL) { PyObject_SetAttrString(tbv, \"__qualname__\", qn); Py_DECREF(qn); }")
                out.append("            }")
            out.append("            PyObject_SetAttrString(tbv, \"__module__\", PyUnicode_FromString(" + q + module_name + q + "));")
            out.append("            PyErr_Clear();")
            # **装饰器** ✅：按**逆序**应用 ✅（@a @b def f -> a(b(f)) ✅），与 CPython 一致 ✅
            for segment in reversed(decorators):
                out.append("            {")
                out.append("                PyObject *dec = PyRun_String(" + c_string(segment) + ", Py_eval_input, cratix_globals, cratix_globals);")
                out.append("                if (dec == NULL) { Py_DECREF(tbv); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
                out.append("                PyObject *nv = PyObject_CallOneArg(dec, tbv);")
                out.append("                Py_DECREF(dec);")
                out.append("                if (nv == NULL) { Py_DECREF(tbv); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
                out.append("                Py_DECREF(tbv);")
                out.append("                tbv = nv;")
                out.append("            }")
            out.append("            if (PyObject_SetAttrString(cls, " + q + method_name + q + ", tbv) < 0) { Py_DECREF(tbv); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(tbv);")
            out.append("        }")
        # **类属性** ✅
        for attr_name, attr_ir in cls.get("attrs", []):
            out.append("        {")
            out.append("            PyObject *av = " + const_expr(attr_ir.get("value")) + ";")
            out.append("            if (av == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            if (PyObject_SetAttrString(cls, " + q + attr_name + q + ", av) < 0) { Py_DECREF(av); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(av);")
            out.append("        }")

        # **原生方法描述符** ✅ —— 建类后挂上去 ✅（PyDescr_NewMethod 需要类对象 ✅）。
        # 好处 ✅：调用时 `self` 由**描述符**绑定 ✅ -> 省掉 Python 跳板帧 ✅。
        desc_index = 0
        for method_name, _gen in cls.get("methods", []):
            if method_name not in descriptors:
                continue
            out.append("        {")
            out.append("            PyObject *cratix_d = PyDescr_NewMethod((PyTypeObject *)cls, &cratix_cls_" + c_ident(cls["name"]) + "_desc[" + str(desc_index) + "]);")
            out.append("            if (cratix_d == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            if (PyObject_SetAttrString(cls, " + q + method_name + q + ", cratix_d) < 0) { Py_DECREF(cratix_d); Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(cratix_d);")
            out.append("        }")
            desc_index += 1
        # **类装饰器** ✅：建类之后按逆序应用 ✅（@a @b class C -> a(b(C)) ✅）
        for segment in reversed(cls.get("decorators") or []):
            out.append("        {")
            out.append("            PyObject *dec = PyRun_String(" + c_string(segment) + ", Py_eval_input, cratix_globals, cratix_globals);")
            out.append("            if (dec == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            PyObject *nv = PyObject_CallOneArg(dec, cls);")
            out.append("            Py_DECREF(dec);")
            out.append("            if (nv == NULL) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(cls);")
            out.append("            cls = nv;")
            out.append("        }")
        out.append("        if (PyDict_SetItemString(cratix_globals, " + q + cls["name"] + q + ", cls) < 0) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
        out.append("        Py_DECREF(cls);")
        out.append("    }")
    out.append("    return m;")
    out.append("}")
    if alias_init:
        out.append("")
        out.append("PyMODINIT_FUNC " + alias_init + "(void) {")
        out.append("    return " + init_symbol + "();")
        out.append("}")
    # **常量池的静态声明** ✅ —— 放在 include 之后 ✅（必须在文件作用域 ✅）
    emit_pool_statics(out, consts)
    # **实例结构体也必须在函数体之前** ✅ —— 属性快路径要按偏移解引用 ✅
    declarations = []
    for cls in (classes or []):
        class_slots = list(cls.get("slots") or ())
        class_struct = "cratix_inst_" + c_ident(cls["name"])
        declarations.append("typedef struct {")
        declarations.append("    PyObject_HEAD")
        for index, _slot in enumerate(class_slots):
            declarations.append("    PyObject *s" + str(index) + ";")
        declarations.append("} " + class_struct + ";")
        declarations.append("static PyObject *cratix_type_" + c_ident(cls["name"]) + " = NULL;")
        declarations.append("")
    if declarations:
        index = 0
        for i, line in enumerate(out):
            if line.startswith("#include"):
                index = i + 1
        out[index:index] = [""] + declarations
    return nl.join(out) + nl

def generate_module(module_name: str, init_symbol: str, functions: list, key_names: list,
                    module_gen=None, alias_init: str | None = None, func_meta=None,
                    classes=None, consts=None) -> str:
    """生成整个模块的 C 代码 ✅（多阶段初始化 ✅ —— 与 stub 的加载桥契合 ✅）。"""
    nl = chr(10)
    q = chr(34)
    out = []
    out.append("/* 由 cratix AOT 编译器生成 —— 请勿手改 ✅ */")
    out.append("#include <Python.h>")
    out.append("#include " + q + "cratix_runtime.h" + q)
    pass


    out.append("")
    out.append("static PyObject *cratix_globals = NULL;")
    out.append("static PyObject *cratix_builtins = NULL;")
    out.append("static PyObject *cratix_builtins_module = NULL;")
    out.append("")
    for key in sorted(set(key_names)):
        out.append("static PyObject *cratix_key_" + c_ident(key) + " = NULL;")
    out.append("")
    # **代码生成覆盖所有函数** ✅（含类方法 ✅ —— 它们的 C 代码要有 ✅）
    all_gens = list(functions)
    for cls in (classes or []):
        for _method_name, gen in cls.get("methods", []):
            all_gens.append(gen)
    for gen in all_gens:
        out.append(generate_function(gen))
        out.append("")
    out.append("static PyMethodDef cratix_methods[] = {")
    for gen in functions:
        cast = "(PyCFunction)(void(*)(void))cratix_fn_" + c_ident(gen.func_name)
        # **元数据保真** ✅：ml_doc 带文本签名 + 文档 ✅（inspect.signature 可用 ✅）
        _doc = method_doc(gen.func_name, (func_meta or {}).get(gen.func_name))
        out.append("    {" + q + gen.func_name + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, " + c_string(_doc) + "},")
    out.append("    {NULL, NULL, 0, NULL}")
    out.append("};")
    out.append("")
    # **exec 槽位的调用约定是 int ✅** —— CPython 按 `int (*)(PyObject *)` 调用 ✅
    # 返回 0 = 成功 ✅ / -1 = 失败（异常已设置 ✅）。
    # 误写成 PyObject* ✅ → 指针被当作 int 解释 ✅ → 非零 → "failed without setting an exception" ✅
    out.append("static int cratix_exec(PyObject *m) {")
    body_decls = []
    body_cleanup = []
    if module_gen is not None:
        body_decls, body_lines, body_cleanup = module_body_parts(module_gen)
        out.extend(body_decls)
    out.append("    cratix_globals = PyModule_GetDict(m);")
    out.append("    if (cratix_globals == NULL) return -1;")
    # 内建字典 ✅：用 PyImport_AddModule("builtins") ✅ ——
    # PyEval_GetBuiltins() 在 3.13+ 已软废弃 ✅ 且依赖当前帧 ✅（模块 exec 期不一定有 ✅）
    out.append("    cratix_builtins_module = PyImport_AddModule(\"builtins\");")
    out.append("    if (cratix_builtins_module == NULL) return -1;")
    out.append("    cratix_builtins = PyModule_GetDict(cratix_builtins_module);")
    out.append("    if (cratix_builtins == NULL) return -1;")
    for key in sorted(set(key_names)):
        out.append("    cratix_key_" + c_ident(key) + " = PyUnicode_InternFromString(" + q + key + q + ");")
        out.append("    if (cratix_key_" + c_ident(key) + " == NULL) return -1;")
    if module_gen is not None:
        out.extend(body_lines)
        out.extend(body_cleanup)
        out.append("    return 0;")
        out.append("cratix_error:")
        out.extend(body_cleanup)
        out.append("    return -1;")
    else:
        out.append("    return 0;")
    out.append("}")
    out.append("")
    out.append("static PyModuleDef_Slot cratix_slots[] = {")
    out.append("    {Py_mod_exec, cratix_exec},")
    out.append("    {0, NULL}")
    out.append("};")
    out.append("")
    out.append("static struct PyModuleDef cratix_module = {")
    out.append("    PyModuleDef_HEAD_INIT,")
    out.append("    " + q + module_name + q + ",")
    out.append("    NULL,")
    # 多阶段初始化要求 m_size >= 0 ✅（-1 是单阶段的约定 ✅ -> 会报 SystemError ✅）
    out.append("    0,")
    out.append("    cratix_methods,")
    out.append("    cratix_slots,")
    out.append("    NULL, NULL, NULL")
    out.append("};")
    out.append("")
    out.append("PyMODINIT_FUNC " + init_symbol + "(void) {")
    out.append("    return PyModuleDef_Init(&cratix_module);")
    out.append("}")
    if alias_init:
        # **别名导出** ✅（仅开发期 ✅）—— 标准扩展加载器按**模块名末段**找 PyInit ✅
        # （我们用哈希符号 ✅ -> 名字不匹配 ✅）-> 打包后由 cratix_loader 显式调用 ✅ 不需要别名 ✅
        out.append("")
        out.append("PyMODINIT_FUNC " + alias_init + "(void) {")
        out.append("    return " + init_symbol + "();")
        out.append("}")
    return nl.join(out) + nl
class FunctionGen:
    """生成一个函数的 C 代码 ✅。"""

    def __init__(self, func_name, params, body, scope, consts=None, use_pool=True,
                 class_methods=None, self_attr_writes=None, method_mode=False,
                 known_classes=None, enclosing_class=None, package="", lambda_names=None,
                 cell_names=None, inner_sites=None, closure_names=None, lambda_captures=None,
                 genexp_captures=None):
        # **所在模块的包名** ✅（相对导入要解析成绝对名 ✅）
        self.package = package or ""
        # **lambda 名字表** ✅（(行, 列) -> 模块级函数名 ✅）
        self.lambda_names = dict(lambda_names or {})
        # **闭包** ✅：本函数里"住在 cell 字典里"的局部 ✅ / 嵌套 def 的登记表 ✅ / 自己捕获的名字 ✅
        self.cell_names = set(cell_names or ())
        self.inner_sites = dict(inner_sites or {})
        # **lambda 捕获表** ✅（(行, 列) -> 捕获的名字 ✅）
        self.lambda_captures = dict(lambda_captures or {})
        # **生成器表达式的捕获表** ✅（(行, 列) -> 捕获的名字 ✅）
        self.genexp_captures = dict(genexp_captures or {})
        self.closure_names = set(closure_names or ())
        self.cells_var = ""
        self.func_name = func_name
        # **原生方法描述符模式** ✅：`self` 从 C 的 `self` 参数拿 ✅（不再是 args[0] ✅）
        self.method_mode = bool(method_mode)
        # **直调优化** ✅：本类已 AOT 的方法（名字 -> C 函数名 ✅）与"被赋过值的 self 属性" ✅。
        # 只有**没被 self.X = ... 覆盖**的方法才敢直调 ✅（否则会绕过实例属性 ✅ -> 语义偏差 ✅）。
        # **保持引用** ✅（不复制 ✅）—— 类方法是一个个生成的 ✅，
        # 复制的话后面生成的兄弟方法看不见 ✅（直调只能用到"已经生成"的 ✅，避免引用未定义符号 ✅）。
        self.class_methods = class_methods if class_methods is not None else {}
        self.self_attr_writes = set(self_attr_writes or ())
        # **常量池** ✅（同一模块的生成器共用一个 ✅ —— 见 driver ✅）
        self.consts = consts if consts is not None else ConstantPool()
        # **A/B 开关** ✅：关掉就退回"每次新建" ✅（基准对照用 ✅，产品默认开 ✅）
        self.use_pool = use_pool and consts is not None
        self.params = params
        self.body = body
        self.scope = scope
        self.lines: list[str] = []
        self.tmp_index = 0
        self.tmp_names: list[str] = []
        self.helper_names: list[str] = []
        self.int_helpers: list[str] = []
        self.declared_attrs: set = set()
        self.try_blocks: list = []
        self.with_blocks: list = []
        # **finally 块栈** ✅ —— return/break/continue 要先把它们跑掉 ✅
        self.finally_blocks: list = []
        # **所有 finally 块** ✅（分派处要按编号找到它 ✅ —— 生成结束时栈已清空 ✅）
        self.finally_all: list = []
        # **生成器表达式的辅助函数** ✅（名字 -> 源码 ✅ —— 模块 init 里建出来 ✅）
        self.genexp_sources: dict = {}
        # **类信息** ✅：名字 -> {属性名: 槽位下标} ✅（属性快路径用 ✅）
        self.known_classes: dict = dict(known_classes or {})
        # 本方法所属的类 ✅（`self` 的静态类型 ✅）
        self.enclosing_class = enclosing_class
        # **局部变量的静态类型** ✅（只做乐观推断 ✅ —— 运行时还有 type check 兜底 ✅）
        self.local_types: dict = {}
        # **推导式作用域栈** ✅ —— 每层推导式一个 {名字: C 临时变量} ✅
        # （Python 3 的推导式有**独立作用域** ✅ -> 目标名不能污染函数局部 ✅）
        self.comp_scopes: list = []
        # **用到的模块全局/内建名字** ✅ —— 编译期收集 ✅，模块 init 里 intern 成静态键 ✅
        # （否则每次取名字都要 `PyUnicode_InternFromString` ✅ 真开销 ✅）
        self.global_names: set = set()
        self.loop_stack: list = []
        self.loop_index = 0
        self.label_index = 0

    # ---------- 基础设施 ----------
    def emit(self, line: str) -> None:
        self.lines.append("    " + line)

    def new_tmp(self) -> str:
        name = "cratix_t" + str(self.tmp_index)
        self.tmp_index += 1
        self.tmp_names.append(name)
        return name

    def new_helper(self) -> str:
        """额外的清理变量 ✅（迭代器等 ✅）—— 也要在出口 DECREF ✅。"""
        name = "cratix_h" + str(self.label_index)
        self.label_index += 1
        self.helper_names.append(name)
        return name

    def local_var(self, name: str) -> str:
        return c_ident(name)

    def new_int_helper(self) -> str:
        """int 临时变量 ✅（比较结果等 ✅）。"""
        name = "cratix_i" + str(self.label_index)
        self.label_index += 1
        self.int_helpers.append(name)
        return name

    # ---------- 参数绑定 ----------
    def emit_binding(self) -> None:
        """把 args/kwargs 绑定到参数变量 ✅。"""
        params = self.params
        names = list(params.get("names", []))
        defaults = list(params.get("defaults", []))
        kwdefaults = list(params.get("kwdefaults", []))
        kwonly = list(params.get("kwonly", []))
        vararg = params.get("vararg")
        kwarg = params.get("kwarg")
        # **方法描述符模式** ✅：self 从 C 的 `self` 拿 ✅ -> 位置参数整体左移一位 ✅
        base = 1 if (self.method_mode and names and names[0] == "self") else 0
        n_pos = len(names)

        self.emit("/* --- 参数绑定 --- */")
        pass
        self.emit("PyObject *cratix_kw = NULL;")
        self.emit("PyObject *cratix_kwv = NULL;")
        if base:
            self.emit("PyObject *cratix_shifted = NULL;")
        # **空 kwargs 不复制** ✅ —— 类方法的跳板是 `def tb(self, *args, **kwargs)` ✅，
        # 调它时 kwargs 永远是个 dict ✅（哪怕是空 dict ✅）→ 每次都 PyDict_Copy ✅（一次真分配 ✅）。
        self.emit("if (kwargs != NULL && PyDict_GET_SIZE(kwargs) > 0) {")
        self.emit("    cratix_kw = PyDict_Copy(kwargs);")
        self.emit("    if (cratix_kw == NULL) goto cratix_error;")
        self.emit("}")

        if base:
            # **self 从 C self 拿** ✅（描述符调用 ✅）；
            # 兜底 ✅：`tp_new` 是 unbound 调用 ✅（self 在 args[0] ✅）-> 这里才复制一次 ✅。
            var = self.local_var(names[0])
            self.emit("/* self（方法描述符 ✅）*/")
            self.emit("if (self != NULL) {")
            self.emit("    " + var + " = self;")
            self.emit("    Py_INCREF(" + var + ");")
            self.emit("} else {")
            self.emit("    if (PyTuple_GET_SIZE(args) < 1) {")
            self.emit("        PyErr_SetString(PyExc_TypeError, \"cratix: missing self\");")
            self.emit("        goto cratix_error;")
            self.emit("    }")
            self.emit("    " + var + " = PyTuple_GET_ITEM(args, 0);")
            self.emit("    Py_INCREF(" + var + ");")
            self.emit("    cratix_shifted = PyTuple_GetSlice(args, 1, PyTuple_GET_SIZE(args));")
            self.emit("    if (cratix_shifted == NULL) goto cratix_error;")
            self.emit("    args = cratix_shifted;")
            self.emit("}")

        for i, name in enumerate(names):
            if base and i == 0:
                continue
            var = self.local_var(name)
            offset = i - base
            self.emit("/* " + name + " */")
            self.emit("if (" + str(offset) + " < PyTuple_GET_SIZE(args)) {")
            self.emit("    " + var + " = PyTuple_GET_ITEM(args, " + str(offset) + ");")
            self.emit("    Py_INCREF(" + var + ");")
            self.emit("} else {")
            self.emit("    cratix_kwv = cratix_kw ? PyDict_GetItemWithError(cratix_kw, cratix_key_" + c_ident(name) + ") : NULL;")
            self.emit("    if (cratix_kwv != NULL) {")
            self.emit("        " + var + " = cratix_kwv;")
            self.emit("        Py_INCREF(" + var + ");")
            self.emit("        PyDict_DelItem(cratix_kw, cratix_key_" + c_ident(name) + ");")
            self.emit("    } else {")
            default = defaults[i] if i < len(defaults) else None
            if default is None:
                self.emit("        PyErr_Format(PyExc_TypeError, \"cratix: missing required argument: %s\", " + c_string(name) + ");")
                self.emit("        goto cratix_error;")
            else:
                self.emit_value_into(var, default)
            self.emit("    }")
            self.emit("}")

        if vararg:
            var = self.local_var(vararg)
            self.emit("/* *" + vararg + " */")
            self.emit("if (PyTuple_GET_SIZE(args) > " + str(n_pos - base) + ") {")
            self.emit("    " + var + " = PyTuple_GetSlice(args, " + str(n_pos - base) + ", PyTuple_GET_SIZE(args));")
            self.emit("    if (" + var + " == NULL) goto cratix_error;")
            self.emit("} else {")
            self.emit("    " + var + " = PyTuple_New(0);")
            self.emit("    if (" + var + " == NULL) goto cratix_error;")
            self.emit("}")

        for i, name in enumerate(kwonly):
            var = self.local_var(name)
            self.emit("/* " + name + "（仅关键字） */")
            self.emit("cratix_kwv = cratix_kw ? PyDict_GetItemWithError(cratix_kw, cratix_key_" + c_ident(name) + ") : NULL;")
            self.emit("if (cratix_kwv != NULL) {")
            self.emit("    " + var + " = cratix_kwv;")
            self.emit("    Py_INCREF(" + var + ");")
            self.emit("    PyDict_DelItem(cratix_kw, cratix_key_" + c_ident(name) + ");")
            self.emit("} else {")
            default = kwdefaults[i] if i < len(kwdefaults) else None
            if default is None:
                self.emit("    PyErr_Format(PyExc_TypeError, \"cratix: missing required keyword-only argument: %s\", " + c_string(name) + ");")
                self.emit("    goto cratix_error;")
            else:
                self.emit_value_into(var, default)
            self.emit("}")

        if kwarg:
            var = self.local_var(kwarg)
            self.emit("/* **" + kwarg + " */")
            self.emit("if (cratix_kw != NULL) {")
            self.emit("    " + var + " = cratix_kw;")
            self.emit("    cratix_kw = NULL;")
            self.emit("} else {")
            self.emit("    " + var + " = PyDict_New();")
            self.emit("    if (" + var + " == NULL) goto cratix_error;")
            self.emit("}")
        else:
            self.emit("if (cratix_kw != NULL && PyDict_GET_SIZE(cratix_kw) > 0) {")
            self.emit("    PyErr_SetString(PyExc_TypeError, \"cratix: unexpected keyword argument\");")
            self.emit("    goto cratix_error;")
            self.emit("}")
    # ---------- 赋值 ----------
    def emit_value_into(self, dest: str, node) -> None:
        """把表达式的值放进 dest ✅（转移所有权 ✅）。"""
        t = self.emit_expr(node)
        self.emit(dest + " = " + t + ";")
        self.emit(t + " = NULL;")

    def track_local_type(self, node) -> None:
        """**乐观**记录 `name = SomeClass(...)` ✅（生成代码里仍有 type check 兜底 ✅）。"""
        if len(node.get("targets", [])) != 1:
            return
        target = node.get("targets")[0]
        if target is None or target.kind != "name":
            return
        name = target.get("id")
        value = node.kids[0] if node.kids else None
        class_name = None
        if value is not None and value.kind == "call" and value.kids:
            func = value.kids[0]
            if func.kind == "name" and func.get("id") in self.known_classes:
                class_name = func.get("id")
        if class_name is None:
            self.local_types.pop(name, None)
        else:
            self.local_types[name] = class_name

    def receiver_type(self, node):
        """静态推断接收者的类名 ✅（乐观 ✅ —— 生成代码里还有 type check 兜底 ✅）。"""
        if node is None or node.kind != "name":
            return None
        name = node.get("id")
        if name == "self":
            return self.enclosing_class
        return self.local_types.get(name)

    def slot_fast_path(self, receiver, attr):
        """槽位快路径的三元组 ✅（结构体名 ✅ / 字段名 ✅ / 类名 ✅）；不适用返回 None ✅。"""
        class_name = self.receiver_type(receiver)
        if not class_name:
            return None
        slots = self.known_classes.get(class_name)
        if not slots or attr not in slots:
            return None
        return (
            "cratix_inst_" + c_ident(class_name),
            "s" + str(slots[attr]),
            class_name,
        )

    def attr_ref(self, node) -> str:
        """属性名的**缓存引用** ✅ —— interned 字符串 ✅（避免每次新建 ✅ ✅）。"""
        return "cratix_an_" + c_ident(target_attr(node))

    def emit_attr_name(self, node) -> None:
        """惰性初始化 interned 属性名 ✅（函数内 static ✅ ✅ —— 每个函数只声明一次 ✅ ✅）。"""
        ref = self.attr_ref(node)
        if ref in self.declared_attrs:
            return
        self.declared_attrs.add(ref)
        # **声明必须在函数顶部** ✅ —— 写在块里 ✅ 别的分支就看不见 ✅ ✅
        # （实测：then 分支首次用到 ✅ -> 声明落在 then 里 ✅ -> else 分支编译报未声明 ✅ ✅）
        self.emit("if (" + ref + " == NULL) {")
        self.emit("    " + ref + " = PyUnicode_InternFromString(" + c_string(target_attr(node)) + ");")
        self.emit("    if (" + ref + " == NULL) goto cratix_error;")
        self.emit("}")

    def emit_cells_init(self) -> None:
        """**cell 字典** ✅ —— 被内层函数捕获的局部变量放这里 ✅（内层按名字读 ✅ 晚绑定 ✅）。"""
        if not self.cell_names:
            return
        self.cells_var = self.new_tmp()
        self.emit(self.cells_var + " = PyDict_New();")
        self.emit("if (" + self.cells_var + " == NULL) goto cratix_error;")
        # **只预置"参数"** ✅ —— 函数体里赋值的名字此刻还是 NULL ✅，
        # 往字典里塞 NULL 会直接崩 ✅（实测：访问违例 ✅）；它们的值由**赋值路径**镜像进去 ✅。
        for name in sorted(self.cell_names & set(self.scope.params)):
            var = self.local_var(name)
            self.emit("if (PyDict_SetItemString(" + self.cells_var + ", " + c_string(name) + ", " + var + ") < 0) goto cratix_error;")

    def emit_inner_def(self, node) -> None:
        """**嵌套 def** ✅ —— 取模块级原生函数 ✅，有捕获就 `functools.partial(fn, cells)` ✅。"""
        site = self.inner_sites.get(node.get("key"))
        if site is None:
            raise Unsupported("嵌套定义（未登记）")
        fn = self.new_tmp()
        self.emit(fn + " = PyDict_GetItemString(cratix_globals, " + c_string(site["name"]) + ");")
        self.emit("if (" + fn + " == NULL) {")
        self.emit("    PyErr_SetString(PyExc_NameError, \"cratix: closure helper missing\");")
        self.emit("    goto cratix_error;")
        self.emit("}")
        value = self.new_tmp()
        if site["captures"]:
            if not self.cells_var:
                raise Unsupported("嵌套定义捕获了变量但没有 cell 字典")
            fmod = self.new_tmp()
            part = self.new_tmp()
            self.emit(fmod + " = PyImport_ImportModule(\"functools\");")
            self.emit("if (" + fmod + " == NULL) goto cratix_error;")
            self.emit(part + " = PyObject_GetAttrString(" + fmod + ", \"partial\");")
            self.emit("Py_DECREF(" + fmod + ");")
            self.emit(fmod + " = NULL;")
            self.emit("if (" + part + " == NULL) goto cratix_error;")
            self.emit(value + " = PyObject_CallFunctionObjArgs(" + part + ", " + fn + ", " + self.cells_var + ", NULL);")
            self.emit("Py_DECREF(" + part + ");")
            self.emit(part + " = NULL;")
            self.emit("if (" + value + " == NULL) goto cratix_error;")
        else:
            self.emit(value + " = Py_NewRef(" + fn + ");")
        var = self.local_var(site["def_name"])
        self.emit("Py_XDECREF(" + var + ");")
        self.emit(var + " = " + value + ";")
        self.emit(value + " = NULL;")
        # **被更内层捕获** ✅ -> 也要镜像进 cell 字典 ✅
        # （嵌套 def 的"赋值"没走 emit_assign ✅ -> 以前漏了 ✅ -> 内层 KeyError ✅ 实测 ✅）
        if site["def_name"] in self.cell_names and self.cells_var:
            self.emit("if (PyDict_SetItemString(" + self.cells_var + ", " + c_string(site["def_name"]) + ", " + var + ") < 0) goto cratix_error;")

    def bind_name(self, name: str, value_var: str) -> None:
        """把 value_var（**所有权转移** ✅）绑到名字上 ✅ —— 局部写局部 ✅，否则写模块全局 ✅。

        **函数里的 import 必须绑定局部** ✅ —— 以前一律写 globals ✅ ->
        局部变量永远是 NULL ✅ -> 用到就崩 ✅（实测：访问违例 ✅）。
        """
        if name in self.scope.locals_ and name not in self.global_names:
            var = self.local_var(name)
            self.emit("Py_XDECREF(" + var + ");")
            self.emit(var + " = " + value_var + ";")
            self.emit(value_var + " = NULL;")
            return
        self.emit("if (PyDict_SetItemString(cratix_globals, " + c_string(name) + ", " + value_var + ") < 0) goto cratix_error;")
        self.emit("Py_DECREF(" + value_var + ");")
        self.emit(value_var + " = NULL;")

    def emit_try(self, node) -> None:
        """try/except ✅ —— 通过**每函数的 try 栈** + 错误处的 handler 分派 ✅ ✅。

        机制 ✅：
        - 每个 try 块压栈 ✅（记录它的编号 ✅ 与 handler 类型 ✅）；
        - 函数体出错 ✅ → 跳到 cratix_error ✅ → 取栈顶 ✅ → 按编号分派 ✅；
        - handler 体**内联** ✅（这样能看见外层局部变量 ✅ ✅）；
        - handler 跑完 ✅ → 跳到该 try 的结束标签 ✅ ✅（继续执行 ✅）；
        - 没有 handler 匹配 ✅ → 弹栈 ✅ → 继续外层 try ✅ ✅ → 都没有 ✅ → 真错误 ✅ ✅。
        """
        if node.get("finalbody"):
            # **try/.../finally** ✅：先处理内层（body + handlers + else ✅），再包一层 finally ✅
            inner = _TryBody(node)
            self.emit_finally(node.get("finalbody", []), inner)
            return
        handlers = node.get("handlers", [])
        if not handlers:
            raise Unsupported("没有 handler 的 try")
        if len(handlers) > MAX_TRY_HANDLERS:
            raise Unsupported("try 的 handler 过多")
        try_id = len(self.try_blocks)
        # **handler 体单独生成** ✅ —— 放在错误处的分派里 ✅（handler 需要看见外层局部变量 ✅ ✅）；
        # 临时变量 ✅ 此时登记 ✅ → 函数级声明 ✅ ✅ 才会包含它们 ✅ ✅。
        handler_bodies = []
        for handler in handlers:
            saved = self.lines
            self.lines = []
            for stmt in handler.kids:
                self.emit_stmt(stmt)
            handler_bodies.append(self.lines)
            self.lines = saved
        self.try_blocks.append({"handlers": handlers, "bodies": handler_bodies})
        self.emit("{")
        self.emit("    if (cratix_try_n >= " + str(MAX_TRY_DEPTH) + ") {")
        self.emit("        PyErr_SetString(PyExc_RuntimeError, \"cratix: try/with nesting too deep\");")
        self.emit("        goto cratix_error;")
        self.emit("    }")
        self.emit("    int cratix_ti = cratix_try_n++;")
        self.emit("    cratix_try_idx[cratix_ti] = " + str(try_id) + ";")
        self.emit("    cratix_try_nh[cratix_ti] = " + str(len(handlers)) + ";")
        for j, handler in enumerate(handlers):
            htype = handler.get("type")
            if htype is None:
                # **借用引用** ✅ -> 自己拿一份 ✅（弹栈时要 DECREF ✅ ✅）
                self.emit("    Py_INCREF(PyExc_Exception);")
                self.emit("    cratix_try_types[cratix_ti][" + str(j) + "] = PyExc_Exception;")
            else:
                hv = self.emit_expr(htype)
                # **所有权转移给帧** ✅ -> 临时变量置 NULL ✅（否则二次释放 ✅ ✅）
                self.emit("    cratix_try_types[cratix_ti][" + str(j) + "] = " + hv + ";")
                self.emit("    " + hv + " = NULL;")
        for stmt in node.get("body", []):
            self.emit_stmt(stmt)
        self.emit("    cratix_try_n--;")
        for j in range(len(handlers)):
            self.emit("    Py_XDECREF(cratix_try_types[cratix_ti][" + str(j) + "]);")
            self.emit("    cratix_try_types[cratix_ti][" + str(j) + "] = NULL;")
        # **else 只在这条"正常完成"的路上跑** ✅（handler 直接跳到结束标签 ✅ -> 不跑 else ✅）
        for stmt in node.get("orelse", []):
            self.emit_stmt(stmt)
        self.emit("    goto cratix_try_end_" + str(try_id) + ";")
        self.emit("}")
        # **结束标签** ✅ —— handler 跑完后跳到这里 ✅（继续执行 ✅ ✅）
        self.emit("cratix_try_end_" + str(try_id) + ": ;")

    def emit_finally(self, finalbody, inner) -> None:
        """**finally** ✅ —— 四条路都要跑 ✅（正常 ✅ / 异常 ✅ / return-break-continue ✅）。

        做法 ✅：finally 体先生成一份**行列表** ✅ ->
        - 正常路径：body 之后直接内联 ✅；
        - 异常路径：分派里跑它 ✅ 然后**继续抛** ✅；
        - return/break/continue：跳之前把它们（由内向外 ✅）内联 ✅。
        代价是 finally 体被复制几份 ✅ —— 但正确性最好懂 ✅。
        """
        fid = FINALLY_FRAME_BASE + len(self.finally_blocks)
        saved = self.lines
        self.lines = []
        for stmt in finalbody:
            self.emit_stmt(stmt)
        body_lines = self.lines
        self.lines = saved
        self.emit("{")
        self.emit("    if (cratix_try_n >= " + str(MAX_TRY_DEPTH) + ") {")
        self.emit("        PyErr_SetString(PyExc_RuntimeError, \"cratix: try/with nesting too deep\");")
        self.emit("        goto cratix_error;")
        self.emit("    }")
        self.emit("    int cratix_ti = cratix_try_n++;")
        self.emit("    cratix_try_idx[cratix_ti] = " + str(fid) + ";")
        self.finally_blocks.append({"id": fid, "lines": body_lines})
        self.finally_all.append({"id": fid, "lines": body_lines})
        for stmt in inner.statements():
            self.emit_stmt(stmt)
        self.finally_blocks.pop()
        self.emit("    cratix_try_n--;")
        for line in body_lines:
            self.emit(line)
        self.emit("}")
        self.emit("cratix_finally_end_" + str(fid) + ": ;")

    def emit_finally_runaways(self, since_depth: int = 0) -> None:
        """return / break / continue 之前 ✅：把**还在栈上的 finally 体**由内向外跑掉 ✅。"""
        for block in reversed(self.finally_blocks[since_depth:]):
            for line in block["lines"]:
                self.emit(line)


    def emit_try_handlers(self) -> list:
        """错误处的 handler 分派 ✅（switch ✅ —— handler 体需要看见外层局部变量 ✅ ✅）。

        **必须返回行列表** ✅ —— 直接 self.emit ✅ 会追加到 gen.lines ✅，
        而函数体早已 join ✅ ✅ → 分派代码会**丢失** ✅ ✅（实测 ✅）。
        """
        if not self.try_blocks and not self.with_blocks:
            return []
        start = len(self.lines)
        self.emit("    if (cratix_try_n > 0) {")
        self.emit("        int cratix_ti = cratix_try_n - 1;")
        self.emit("        PyObject *cratix_et = NULL, *cratix_ev = NULL, *cratix_etb = NULL;")
        self.emit("        int cratix_m = -1;")
        self.emit("        PyErr_Fetch(&cratix_et, &cratix_ev, &cratix_etb);")
        self.emit("        PyErr_NormalizeException(&cratix_et, &cratix_ev, &cratix_etb);")
        self.emit("        switch (cratix_try_idx[cratix_ti]) {")
        for try_id, block in enumerate(self.try_blocks):
            handlers = block["handlers"]
            self.emit("            case " + str(try_id) + ": {")
            self.emit("                for (int j = 0; j < " + str(len(handlers)) + "; j++) {")
            self.emit("                    if (cratix_try_types[cratix_ti][j] != NULL && PyErr_GivenExceptionMatches(cratix_ev, cratix_try_types[cratix_ti][j])) { cratix_m = j; break; }")
            self.emit("                }")
            self.emit("                if (cratix_m < 0) {")
            self.emit("                    goto cratix_no_match;")
            self.emit("                }")
            # **命中** ✅ -> 先弹栈 ✅ + 释放类型 ✅（原来漏了 ✅ -> 栈越积越多 ✅ ✅）
            self.emit("                cratix_try_n--;")
            for j in range(len(handlers)):
                self.emit("                Py_XDECREF(cratix_try_types[cratix_ti][" + str(j) + "]);")
                self.emit("                cratix_try_types[cratix_ti][" + str(j) + "] = NULL;")
            for j, handler in enumerate(handlers):
                name = handler.get("name")
                if name:
                    self.emit("                if (cratix_m == " + str(j) + ") {")
                    self.emit("                    Py_INCREF(cratix_ev);")
                    self.emit("                    " + self.local_var(name) + " = cratix_ev;")
                    self.emit("                }")
            # **释放三元组** ✅（名字已 INCREF ✅ 不会悬空 ✅）
            self.emit("                Py_XDECREF(cratix_et); Py_XDECREF(cratix_ev); Py_XDECREF(cratix_etb);")
            self.emit("                cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
            for j in range(len(handlers)):
                self.emit("                if (cratix_m == " + str(j) + ") {")
                for line in block["bodies"][j]:
                    self.emit(line)
                self.emit("                    goto cratix_try_end_" + str(try_id) + ";")
                self.emit("                }")
            self.emit("                break;")
            self.emit("            }")
        for block in self.finally_all:
            fid = block["id"]
            self.emit("            case " + str(fid) + ": {")
            self.emit("                cratix_try_n--;")
            for line in block["lines"]:
                self.emit(line)
            # **跑完 finally 继续抛** ✅（finally 里的 return 会自己跳走 ✅）
            self.emit("                PyErr_Restore(cratix_et, cratix_ev, cratix_etb);")
            self.emit("                cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
            self.emit("                goto cratix_error;")
            self.emit("                break;")
            self.emit("            }")
        for wid in self.with_blocks:
            self.emit("            case " + str(wid) + ": {")
            self.emit("                if (cratix_with_mgr[cratix_ti] == NULL) { goto cratix_no_match; }")
            # **NULL 不能直接传给 Py_BuildValue** ✅ —— traceback 常常是 NULL ✅
            # （实测：SystemError: NULL object passed to Py_BuildValue ✅）
            self.emit("                PyObject *cratix_ea = cratix_et != NULL ? cratix_et : Py_None;")
            self.emit("                PyObject *cratix_eb = cratix_ev != NULL ? cratix_ev : Py_None;")
            self.emit("                PyObject *cratix_ec = cratix_etb != NULL ? cratix_etb : Py_None;")
            self.emit("                PyObject *cratix_wr = PyObject_CallMethod(cratix_with_mgr[cratix_ti], \"__exit__\", \"OOO\", cratix_ea, cratix_eb, cratix_ec);")
            self.emit("                cratix_try_n--;")
            self.emit("                if (cratix_wr == NULL) {")
            self.emit("                    Py_XDECREF(cratix_et); Py_XDECREF(cratix_ev); Py_XDECREF(cratix_etb);")
            self.emit("                    cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
            self.emit("                    goto cratix_error;")
            self.emit("                }")
            self.emit("                cratix_m = PyObject_IsTrue(cratix_wr);")
            self.emit("                Py_DECREF(cratix_wr);")
            self.emit("                if (cratix_m < 0) {")
            self.emit("                    Py_XDECREF(cratix_et); Py_XDECREF(cratix_ev); Py_XDECREF(cratix_etb);")
            self.emit("                    cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
            self.emit("                    goto cratix_error;")
            self.emit("                }")
            self.emit("                if (cratix_m > 0) {")
            self.emit("                    Py_XDECREF(cratix_et); Py_XDECREF(cratix_ev); Py_XDECREF(cratix_etb);")
            self.emit("                    cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
            self.emit("                    goto cratix_with_end_" + str(wid) + ";")
            self.emit("                }")
            # **不抑制** ✅ -> 帧已弹 ✅ -> 直接把异常交给外层 ✅
            # （原来走 cratix_no_match ✅ -> 又弹一次 ✅ -> 把外层 try 帧也弹掉了 ✅ ✅）
            self.emit("                PyErr_Restore(cratix_et, cratix_ev, cratix_etb);")
            self.emit("                cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
            self.emit("                goto cratix_error;")
            self.emit("                break;")
            self.emit("            }")
        self.emit("        }")
        self.emit("cratix_no_match:")
        self.emit("        cratix_try_n--;")
        self.emit("        PyErr_Restore(cratix_et, cratix_ev, cratix_etb);")
        self.emit("        cratix_et = NULL; cratix_ev = NULL; cratix_etb = NULL;")
        self.emit("        goto cratix_error;")
        self.emit("    }")
        return self.lines[start:]

    def emit_assign(self, target, value_var: str) -> None:
        """赋值到目标 ✅（value_var 的所有权转移给目标 ✅）。"""
        kind = target.kind
        if kind == "name":
            name = target.get("id")
            # **推导式目标** ✅ -> 写推导式的临时变量 ✅（不是函数局部 ✅）
            comp_var = self.comp_var(name)
            if comp_var is not None:
                self.emit("Py_XSETREF(" + comp_var + ", " + value_var + ");")
                self.emit(value_var + " = NULL;")
                return
            if name in self.scope.locals_:
                var = self.local_var(name)
                self.emit("Py_XDECREF(" + var + ");")
                self.emit(var + " = " + value_var + ";")
                if name in self.cell_names and self.cells_var:
                    # **被内层捕获** ✅ -> 同步进 cell 字典 ✅
                    self.emit("if (PyDict_SetItemString(" + self.cells_var + ", " + c_string(name) + ", " + var + ") < 0) goto cratix_error;")
            else:
                self.emit("if (PyDict_SetItemString(cratix_globals, " + c_string(name) + ", " + value_var + ") < 0) goto cratix_error;")
                self.emit("Py_DECREF(" + value_var + ");")
            self.emit(value_var + " = NULL;")
            return
        if kind == "attr":
            obj = self.emit_expr(target.kids[0])
            self.emit_attr_name(target)
            fast = self.slot_fast_path(target.kids[0], target.get("attr"))
            if fast is None:
                self.emit("if (PyObject_SetAttr(" + obj + ", " + self.attr_ref(target) + ", " + value_var + ") < 0) goto cratix_error;")
                self.emit("Py_DECREF(" + value_var + ");")
                self.emit(value_var + " = NULL;")
                return
            struct_name, field, class_name = fast
            # **槽位写入快路径** ✅（类型对得上就按偏移直接写 ✅；否则退回通用路径 ✅）
            self.emit("if (Py_TYPE(" + obj + ") == (PyTypeObject *)cratix_type_" + c_ident(class_name) + ") {")
            self.emit("    PyObject *cratix_old = ((" + struct_name + " *)" + obj + ")->" + field + ";")
            self.emit("    ((" + struct_name + " *)" + obj + ")->" + field + " = " + value_var + ";")
            self.emit("    " + value_var + " = NULL;")
            self.emit("    Py_XDECREF(cratix_old);")
            self.emit("} else {")
            self.emit("    if (PyObject_SetAttr(" + obj + ", " + self.attr_ref(target) + ", " + value_var + ") < 0) goto cratix_error;")
            self.emit("    Py_DECREF(" + value_var + ");")
            self.emit("    " + value_var + " = NULL;")
            self.emit("}")
            return
        if kind == "subscript":
            obj = self.emit_expr(target.kids[0])
            key = self.emit_expr(target.kids[1])
            self.emit("if (PyObject_SetItem(" + obj + ", " + key + ", " + value_var + ") < 0) goto cratix_error;")
            self.emit("Py_DECREF(" + value_var + ");")
            self.emit(value_var + " = NULL;")
            return
        if kind in ("tuple", "list"):
            self.emit_unpack(target, value_var)
            return
        raise Unsupported("赋值目标：" + kind)

    def emit_unpack(self, target, value_var: str) -> None:
        """序列解包 ✅（v1 不支持星号解包 ✅ → 整函数回退 ✅）。"""
        kids = list(target.kids)
        for kid in kids:
            if kid.kind == "starred":
                raise Unsupported("星号解包")
        seq = self.new_tmp()
        self.emit(seq + " = PySequence_Fast(" + value_var + ", NULL);")
        self.emit("if (" + seq + " == NULL) goto cratix_error;")
        self.emit("if (PyTuple_GET_SIZE(" + seq + ") != " + str(len(kids)) + ") {")
        self.emit("    PyErr_SetString(PyExc_ValueError, \"cratix: unpack count mismatch\");")
        self.emit("    goto cratix_error;")
        self.emit("}")
        for i, kid in enumerate(kids):
            item = self.new_tmp()
            self.emit(item + " = PyTuple_GET_ITEM(" + seq + ", " + str(i) + ");")
            self.emit("Py_INCREF(" + item + ");")
            self.emit_assign(kid, item)
        self.emit("Py_DECREF(" + seq + ");")
        self.emit(seq + " = NULL;")

    # ---------- 循环 ✅ ----------
    def begin_loop(self):
        """开一个循环 ✅ —— 记录**栈深度** ✅ 与 **break 标志** ✅。

        - 深度 ✅：break / continue 时要退掉循环里压的 with 帧 ✅；
        - break 标志 ✅：Python 的 `for ... else` ✅ 只在**没有 break** 时跑 else ✅ ✅。
        """
        idx = self.loop_index
        self.loop_index += 1
        depth_var = "cratix_ldepth_" + str(idx)
        broke_var = "cratix_lbroke_" + str(idx)
        self.emit("{")
        self.emit("    int " + depth_var + " = cratix_try_n;")
        self.emit("    int " + broke_var + " = 0;")
        self.loop_stack.append({
            "depth": depth_var,
            "broke": broke_var,
            "tmp": self.tmp_index,
            "helpers": len(self.helper_names),
            # **循环开始时已有的 finally 块数** ✅（break/continue 只跑"循环体内"的 ✅）
            "finally": len(self.finally_blocks),
        })
        return depth_var, broke_var

    def emit_tmp_cleanup(self, mark: int, indent: str = "    ") -> None:
        """释放**循环体/推导式内**产生的临时变量 ✅（每次迭代都要放 ✅）。

        不这么做 ✅：临时变量是**函数级复用**的 C 变量 ✅ ——
        每轮直接覆盖 ✅ -> **每轮泄漏一个引用** ✅（实测：长循环内存线性增长 ✅ + 分配器压力 ✅）。
        """
        for name in self.tmp_names[mark:]:
            self.emit(indent + "Py_XDECREF(" + name + ");")
            self.emit(indent + name + " = NULL;")

    def emit_helper_cleanup(self, mark: int, indent: str = "    ") -> None:
        """释放**循环体内**产生的辅助变量 ✅（with 的管理器等 ✅）。

        for 的迭代器在进入循环**之前**创建 ✅ -> 不在范围内 ✅（不能每轮释放 ✅）。
        """
        for name in self.helper_names[mark:]:
            self.emit(indent + "Py_XDECREF(" + name + ");")
            self.emit(indent + name + " = NULL;")

    def end_loop_body(self) -> None:
        """**循环体末尾** ✅：放掉本轮临时变量 ✅（每次迭代都跑 ✅）。"""
        if not self.loop_stack:
            return
        frame = self.loop_stack[-1]
        self.emit_tmp_cleanup(frame["tmp"], "    ")
        self.emit_helper_cleanup(frame["helpers"], "    ")

    def end_loop(self, broke_var, orelse) -> None:
        """收尾循环 ✅ —— else 分支只在**没有 break** 时执行 ✅（CPython 语义 ✅）。"""
        self.loop_stack.pop()
        if orelse:
            self.emit("    if (!" + broke_var + ") {")
            for stmt in orelse:
                self.emit_stmt(stmt)
            self.emit("    }")
        self.emit("}")

    def emit_unwind_to(self, depth_var: str) -> None:
        """退栈到指定深度 ✅（break / continue ✅ —— 只跑 with 的 __exit__ ✅ 不跑 handler ✅）。"""
        self.emit("while (cratix_try_n > " + depth_var + ") {")
        self.emit("    int cratix_ui = cratix_try_n - 1;")
        self.emit("    int cratix_uk = cratix_try_idx[cratix_ui];")
        self.emit("    cratix_try_n--;")
        self.emit("    if (cratix_uk >= " + str(WITH_FRAME_BASE) + ") {")
        # **cratix_with_mgr 是借用引用** ✅（所有权在 mgr 临时变量 ✅ -> 出口统一 DECREF ✅）
        self.emit("        if (cratix_with_mgr[cratix_ui] != NULL) {")
        self.emit("            PyObject *cratix_ur = PyObject_CallMethod(cratix_with_mgr[cratix_ui], \"__exit__\", \"OOO\", Py_None, Py_None, Py_None);")
        self.emit("            if (cratix_ur == NULL) { result = NULL; goto cratix_error; }")
        self.emit("            Py_DECREF(cratix_ur);")
        self.emit("        }")
        self.emit("    }")
        self.emit("}")

    def emit_unwind_code(self) -> list:
        """**退栈段** ✅（return ✅ —— 把所有 with 帧的 __exit__ 跑完 ✅）。"""
        return [
            "    while (cratix_try_n > 0) {",
            "        int cratix_ui = cratix_try_n - 1;",
            "        int cratix_uk = cratix_try_idx[cratix_ui];",
            "        cratix_try_n--;",
            "        if (cratix_uk >= " + str(WITH_FRAME_BASE) + ") {",
            "            if (cratix_with_mgr[cratix_ui] != NULL) {",
            "                PyObject *cratix_ur = PyObject_CallMethod(cratix_with_mgr[cratix_ui], \"__exit__\", \"OOO\", Py_None, Py_None, Py_None);",
            "                if (cratix_ur == NULL) { result = NULL; goto cratix_error; }",
            "                Py_DECREF(cratix_ur);",
            "            }",
            "        }",
            "    }",
        ]

    # ---------- with ✅ ----------
    def emit_with(self, node) -> None:
        """with 语句 ✅ —— 走**帧栈** ✅（与 try 共用一套栈 ✅）。

        机制 ✅：
        - 进入 ✅：求值上下文 → 调 `__enter__` ✅ → 绑定目标 ✅；
        - 压帧 ✅：帧类型 = with ✅（记录管理器对象 ✅）；
        - 正常退出 ✅：调 `__exit__(None, None, None)` ✅（返回值忽略 ✅ —— 与 CPython 一致 ✅）；
        - 异常退出 ✅：错误分派处 ✅ 调 `__exit__(异常三元组)` ✅
          → 返回真 ✅ → **抑制异常** ✅ → 跳到 with 结束 ✅；
          → 返回假 ✅ → **继续向外传播** ✅ ✅。
        """
        kids = list(node.kids)
        if not kids or len(kids) % 2 != 0:
            raise Unsupported("with 结构异常")
        pairs = [(kids[i], kids[i + 1]) for i in range(0, len(kids), 2)]
        self.emit_with_chain(pairs, 0, node.get("body", []))

    def emit_with_chain(self, pairs, index: int, body) -> None:
        """多上下文管理器 ✅ —— 嵌套 ✅（第一个在最外层 ✅ —— 与 CPython 一致 ✅）。"""
        ctx, var = pairs[index]
        mgr = self.new_helper()
        enter = self.new_tmp()
        src = self.emit_expr(ctx)
        self.emit(mgr + " = " + src + ";")
        self.emit(src + " = NULL;")
        self.emit("if (" + mgr + " == NULL) goto cratix_error;")
        self.emit(enter + " = PyObject_CallMethod(" + mgr + ", \"__enter__\", NULL);")
        self.emit("if (" + enter + " == NULL) {")
        self.emit("    Py_DECREF(" + mgr + ");")
        self.emit("    " + mgr + " = NULL;")
        self.emit("    goto cratix_error;")
        self.emit("}")
        if var is not None:
            self.emit_assign(var, enter)  # 转移所有权 ✅
        else:
            self.emit("Py_DECREF(" + enter + ");")
            self.emit(enter + " = NULL;")
        wid = WITH_FRAME_BASE + len(self.with_blocks)
        self.with_blocks.append(wid)
        self.emit("{")
        self.emit("    if (cratix_try_n >= " + str(MAX_TRY_DEPTH) + ") {")
        self.emit("        PyErr_SetString(PyExc_RuntimeError, \"cratix: try/with nesting too deep\");")
        self.emit("        goto cratix_error;")
        self.emit("    }")
        self.emit("    int cratix_ti = cratix_try_n++;")
        self.emit("    cratix_try_idx[cratix_ti] = " + str(wid) + ";")
        self.emit("    cratix_with_mgr[cratix_ti] = " + mgr + ";")
        if index + 1 < len(pairs):
            self.emit_with_chain(pairs, index + 1, body)
        else:
            for stmt in body:
                self.emit_stmt(stmt)
        self.emit("    cratix_try_n--;")
        self.emit("    cratix_with_mgr[cratix_ti] = NULL;")
        res = self.new_tmp()
        self.emit("    " + res + " = PyObject_CallMethod(" + mgr + ", \"__exit__\", \"OOO\", Py_None, Py_None, Py_None);")
        self.emit("    if (" + res + " == NULL) {")
        self.emit("        Py_DECREF(" + mgr + ");")
        self.emit("        " + mgr + " = NULL;")
        self.emit("        goto cratix_error;")
        self.emit("    }")
        self.emit("    Py_DECREF(" + res + ");")
        self.emit("    Py_DECREF(" + mgr + ");")
        self.emit("    " + mgr + " = NULL;")
        self.emit("}")
        self.emit("cratix_with_end_" + str(wid) + ": ;")

    # ---------- 语句 ----------
    def emit_stmt(self, node) -> None:
        kind = node.kind
        if kind == "expr_stmt":
            t = self.emit_expr(node.kids[0])
            self.emit("Py_XDECREF(" + t + ");")
            self.emit(t + " = NULL;")
            return
        if kind == "assign":
            value = self.emit_expr(node.kids[0])
            self.track_local_type(node)
            for target in node.get("targets", []):
                self.emit_assign(target, value)
            return
        if kind == "augassign":
            target = node.get("target")
            rhs = self.emit_expr(node.kids[0])
            fn = AUG_FUNCS.get(node.get("op"))
            if fn is None:
                raise Unsupported("复合赋值：" + str(node.get("op")))
            if target.kind == "name" and target.get("id") in self.scope.locals_:
                var = self.local_var(target.get("id"))
                # **结果必须是和 ✅**（旧口径把 RHS 赋给了目标 ✅ —— 值错了 ✅）
                # PyNumber_InPlaceAdd 可能**原地返回同一个对象** ✅（list ✅）
                # → 用 Py_XSETREF ✅（先 DECREF 旧值 ✅ 再接管新引用 ✅）
                tmp = self.new_tmp()
                self.emit(tmp + " = " + fn + "(" + var + ", " + rhs + ");")
                self.emit("if (" + tmp + " == NULL) goto cratix_error;")
                self.emit("Py_XDECREF(" + rhs + ");")
                self.emit(rhs + " = NULL;")
                self.emit("Py_XSETREF(" + var + ", " + tmp + ");")
                self.emit(tmp + " = NULL;")
            else:
                # **非局部目标** ✅（`obj.attr += x` / `d[k] += v` ✅）：
                # 直接展开成「读 + 算 + 写」✅ —— 与 Python 语义一致 ✅（读/写各一次 ✅）。
                fn = AUG_FUNCS.get(node.get("op"))
                if fn is None:
                    raise Unsupported("复合赋值：未知运算符")
                current = self.emit_expr(target)
                other = self.new_tmp()
                self.emit(other + " = " + fn + "(" + current + ", " + rhs + ");")
                self.emit("if (" + other + " == NULL) goto cratix_error;")
                self.emit_assign(target, other)
                return
            return
        if kind == "return":
            if node.kids:
                t = self.emit_expr(node.kids[0])
                self.emit("result = " + t + ";")
                self.emit(t + " = NULL;")
            else:
                self.emit("result = Py_NewRef(Py_None);")
            # **return 先跑 finally** ✅ 再退栈（里面的 with 必须跑 __exit__ ✅ ✅）
            self.emit_finally_runaways()
            self.emit("goto cratix_unwind;")
            return
        if kind == "if":
            test = self.emit_expr(node.kids[0])
            self.emit("if (cratix_truth(" + test + ")) {")
            for stmt in node.get("body", []):
                self.emit_stmt(stmt)
            self.emit("} else {")
            for stmt in node.get("orelse", []):
                self.emit_stmt(stmt)
            self.emit("}")
            return
        if kind == "while":
            depth_var, broke_var = self.begin_loop()
            self.emit("while (1) {")
            test = self.emit_expr(node.kids[0])
            self.emit("    if (!cratix_truth(" + test + ")) break;")
            for stmt in node.get("body", []):
                self.emit_stmt(stmt)
            self.emit("    ")
            self.end_loop_body()
            self.emit("}")
            self.end_loop(broke_var, node.get("orelse", []))
            return
        if kind == "for":
            # **迭代器在进入循环前建好** ✅ —— 这样"循环体内"的辅助变量集合是干净的 ✅
            # （每轮结束要释放体内辅助变量 ✅；迭代器本身不能释放 ✅）
            iter_expr = self.emit_expr(node.kids[1])
            it = self.new_helper()
            depth_var, broke_var = self.begin_loop()
            self.emit(it + " = PyObject_GetIter(" + iter_expr + ");")
            self.emit("if (" + it + " == NULL) goto cratix_error;")
            self.emit("while (1) {")
            item = self.new_tmp()
            self.emit("    " + item + " = PyIter_Next(" + it + ");")
            self.emit("    if (" + item + " == NULL) {")
            self.emit("        if (PyErr_Occurred()) goto cratix_error;")
            self.emit("        break;")
            self.emit("    }")
            self.emit_assign(node.kids[0], item)
            for stmt in node.get("body", []):
                self.emit_stmt(stmt)
            self.emit("    ")
            self.end_loop_body()
            self.emit("}")
            self.emit("Py_DECREF(" + it + ");")
            self.emit(it + " = NULL;")
            self.end_loop(broke_var, node.get("orelse", []))
            return
        if kind == "break":
            if not self.loop_stack:
                raise Unsupported("break 不在循环里")
            frame = self.loop_stack[-1]
            self.emit(frame["broke"] + " = 1;")
            # **循环体内的 finally 先跑** ✅（Python 语义 ✅）
            self.emit_finally_runaways(frame["finally"])
            self.emit_unwind_to(frame["depth"])
            # **跳之前也要放** ✅（否则这条路径上的引用会留到函数出口 ✅）
            self.emit_tmp_cleanup(frame["tmp"])
            self.emit_helper_cleanup(frame["helpers"])
            self.emit("break;")
            return
        if kind == "continue":
            if not self.loop_stack:
                raise Unsupported("continue 不在循环里")
            frame = self.loop_stack[-1]
            self.emit_finally_runaways(frame["finally"])
            self.emit_unwind_to(frame["depth"])
            self.emit_tmp_cleanup(frame["tmp"])
            self.emit_helper_cleanup(frame["helpers"])
            self.emit("continue;")
            return
        if kind == "pass" or kind == "noop":
            return
        if kind == "funcdef":
            # **函数体里的嵌套 def** ✅（模块级/类方法的 def 不进这里 ✅）
            self.emit_inner_def(node)
            return
        if kind == "raise":
            t = self.emit_expr(node.kids[0])
            self.emit("cratix_raise(" + t + ");")
            self.emit("goto cratix_error;")
            return
        if kind == "assert":
            t = self.emit_expr(node.kids[0])
            self.emit("if (!cratix_truth(" + t + ")) {")
            if len(node.kids) > 1 and node.kids[1] is not None:
                msg = self.emit_expr(node.kids[1])
                self.emit("    PyErr_Format(PyExc_AssertionError, \"%s\", PyObject_Str(" + msg + "));")
            else:
                self.emit("    PyErr_SetString(PyExc_AssertionError, \"\");")
            self.emit("    goto cratix_error;")
            self.emit("}")
            return
        if kind == "importfrom":
            module_src = node.get("module") or ""
            level = int(node.get("level") or 0)
            if level == 0:
                # **绝对导入** ✅（level=0 ✅ —— 不要加包名前缀 ✅）
                absolute = module_src
            else:
                base = self.package
                for _ in range(level - 1):
                    base = base.rpartition(".")[0]
                absolute = (base + "." + module_src) if module_src else base
            if absolute == "__future__":
                # **编译指令** ✅ —— 对我们没有运行期含义 ✅（注解不求值 ✅）
                return
            if not absolute:
                raise Unsupported("相对导入没有包上下文")
            key = self.new_tmp()
            self.emit(key + " = " + const_expr(absolute) + ";")
            self.emit("if (" + key + " == NULL) goto cratix_error;")
            mod = self.new_tmp()
            self.emit(mod + " = PyImport_Import(" + key + ");")
            self.emit("if (" + mod + " == NULL) goto cratix_error;")
            for name, asname in node.get("names", []):
                bind = asname or name
                val = self.new_tmp()
                self.emit(val + " = PyObject_GetAttrString(" + mod + ", " + c_string(name) + ");")
                self.emit("if (" + val + " == NULL) {")
                self.emit("    Py_DECREF(" + mod + ");")
                self.emit("    " + mod + " = NULL;")
                self.emit("    goto cratix_error;")
                self.emit("}")
                self.bind_name(bind, val)
            # **子模块也能被找到** ✅（CPython 的 import fromlist 会顺手导入子模块 ✅）
            self.emit("Py_DECREF(" + mod + ");")
            self.emit(mod + " = NULL;")
            return
        if kind == "import":
            # **import 语句** ✅ —— 绑定到模块字典 ✅（= 模块全局 ✅）
            # "import a.b" 不带 asname 时绑定**顶层包** a ✅（与 CPython 一致 ✅）。
            for module_name, asname in node.get("names", []):
                top = module_name.split(".")[0]
                key = self.new_tmp()
                self.emit(key + " = " + const_expr(module_name) + ";")
                self.emit("if (" + key + " == NULL) goto cratix_error;")
                mod = self.new_tmp()
                # **PyImport_Import** ✅（收 PyObject* ✅ —— 完整 API ✅）——
                # `PyImport_ImportModule` 收的是 **char*** ✅ → 传 PyObject* 会把指针当字符串 ✅
                # → 导入名变成垃圾 ✅ → "No module named '\x01'" ✅（实测 ✅）。
                # `PyImport_ImportModuleObject` 在 3.14 **没有导出** ✅ → 用 PyImport_Import ✅。
                self.emit(mod + " = PyImport_Import(" + key + ");")
                self.emit("if (" + mod + " == NULL) goto cratix_error;")
                if asname:
                    bind_name = asname
                    bind_val = mod
                elif top == module_name:
                    bind_name = module_name
                    bind_val = mod
                else:
                    bind_name = top
                    top_key = self.new_tmp()
                    self.emit(top_key + " = " + const_expr(top) + ";")
                    self.emit("if (" + top_key + " == NULL) goto cratix_error;")
                    bind_val = self.new_tmp()
                    self.emit(bind_val + " = PyImport_Import(" + top_key + ");")
                    self.emit("if (" + bind_val + " == NULL) goto cratix_error;")
                self.bind_name(bind_name, bind_val)
            return
        if kind == "try":
            self.emit_try(node)
            return
        if kind == "with":
            self.emit_with(node)
            return
        raise Unsupported("语句：" + kind)
    # ---------- 表达式 ----------
    def emit_fast_builtin(self, node, t) -> bool:
        """内建函数**直译** ✅ —— 绕开通用调用开销 ✅。

        CPython 3.14 的解释器有专门化快路径 ✅；我们走通用 C-API ✅ 会慢 ✅ ——
        把最常见的内建换成直接的 C 调用 ✅ 就能明显改善 ✅ ✅。
        """
        target = node.kids[0]
        if target.kind != "name" or node.get("keywords"):
            return False
        name = target.get("id")
        args = node.kids[1:]
        if name == "len" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PyLong_FromSsize_t(PyObject_Length(" + v + "));")
            self.emit("if (PyErr_Occurred()) goto cratix_error;")
            return True
        if name in ("str", "repr") and len(args) == 1:
            v = self.emit_expr(args[0])
            fn = "PyObject_Str" if name == "str" else "PyObject_Repr"
            self.emit(t + " = " + fn + "(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "int" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PyNumber_Long(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "float" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PyNumber_Float(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "abs" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PyNumber_Absolute(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "type" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PyObject_Type(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "list" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PySequence_List(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "tuple" and len(args) == 1:
            v = self.emit_expr(args[0])
            self.emit(t + " = PySequence_Tuple(" + v + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "isinstance" and len(args) == 2:
            a = self.emit_expr(args[0])
            b = self.emit_expr(args[1])
            self.emit(t + " = PyObject_IsInstance(" + a + ", " + b + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        if name == "hasattr" and len(args) == 2:
            a = self.emit_expr(args[0])
            b = self.emit_expr(args[1])
            self.emit(t + " = PyObject_HasAttrString(" + a + ", PyUnicode_AsUTF8(" + b + "));")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        return False

    def emit_expr(self, node) -> str:
        """表达式 -> C 语句序列 ✅；返回持有结果的临时变量名 ✅。"""
        kind = node.kind
        t = self.new_tmp()
        if kind == "const":
            if self.use_pool:
                # **走常量池** ✅ —— 只 INCREF ✅，不再每次新建 ✅
                name = self.consts.ref(node.get("value"))
                self.emit(t + " = " + name + ";")
                self.emit("Py_INCREF(" + t + ");")
            else:
                self.emit(t + " = " + const_expr(node.get("value")) + ";")
            return t
        if kind == "name":
            name = node.get("id")
            # **推导式局部优先** ✅ —— 由内向外找 ✅（内层遮蔽外层 ✅）
            comp_var = self.comp_var(name)
            if comp_var is not None:
                self.emit(t + " = " + comp_var + ";")
                self.emit("Py_XINCREF(" + t + ");")
                return t
            if name in self.closure_names:
                # **闭包自由变量** ✅ —— 从 cell 字典读 ✅（晚绑定 ✅）
                cells = self.local_var("_cratix_cells")
                self.emit(t + " = PyDict_GetItemString(" + cells + ", " + c_string(name) + ");")
                self.emit("if (" + t + " == NULL) {")
                self.emit("    PyErr_SetString(PyExc_NameError, \"cratix: free variable not bound\");")
                self.emit("    goto cratix_error;")
                self.emit("}")
                self.emit("Py_INCREF(" + t + ");")
                return t
            if name in self.scope.locals_:
                var = self.local_var(name)
                self.emit(t + " = " + var + ";")
                self.emit("Py_XINCREF(" + t + ");")
            else:
                # **走静态键** ✅（模块 init 里 intern 一次 ✅）
                self.global_names.add(name)
                self.emit(
                    t + " = cratix_load_key(cratix_globals, cratix_builtins, cratix_key_"
                    + c_ident(name) + ", " + c_string(name) + ");"
                )
                self.emit("if (" + t + " == NULL) goto cratix_error;")
            return t
        if kind == "attr":
            obj = self.emit_expr(node.kids[0])
            self.emit_attr_name(node)
            fast = self.slot_fast_path(node.kids[0], node.get("attr"))
            if fast is None:
                self.emit(t + " = PyObject_GetAttr(" + obj + ", " + self.attr_ref(node) + ");")
                self.emit("if (" + t + " == NULL) goto cratix_error;")
                return t
            struct_name, field, class_name = fast
            # **槽位快路径** ✅：类型对得上就按偏移直接读 ✅（省掉属性协议 ✅）；
            # 对不上就退回通用查找 ✅ —— 所以这个快路径**永远安全** ✅。
            self.emit("if (Py_TYPE(" + obj + ") == (PyTypeObject *)cratix_type_" + c_ident(class_name) + ") {")
            self.emit("    " + t + " = ((" + struct_name + " *)" + obj + ")->" + field + ";")
            self.emit("    if (" + t + " == NULL) {")
            self.emit("        PyErr_Format(PyExc_AttributeError, \"cratix: unset slot '%s'\", " + c_string(node.get("attr")) + ");")
            self.emit("        goto cratix_error;")
            self.emit("    }")
            self.emit("    Py_INCREF(" + t + ");")
            self.emit("} else {")
            self.emit("    " + t + " = PyObject_GetAttr(" + obj + ", " + self.attr_ref(node) + ");")
            self.emit("    if (" + t + " == NULL) goto cratix_error;")
            self.emit("}")
            return t
        if kind == "subscript":
            obj = self.emit_expr(node.kids[0])
            key = self.emit_expr(node.kids[1])
            self.emit(t + " = PyObject_GetItem(" + obj + ", " + key + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return t
        if kind == "binop":
            left = self.emit_expr(node.kids[0])
            right = self.emit_expr(node.kids[1])
            fn = BINOP_FUNCS.get(node.get("op"))
            if fn is None:
                raise Unsupported("二元运算符：" + str(node.get("op")))
            self.emit(t + " = " + fn + "(" + left + ", " + right + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return t
        if kind == "unaryop":
            operand = self.emit_expr(node.kids[0])
            op = node.get("op")
            if op == "not":
                self.emit(t + " = cratix_truth(" + operand + ") ? Py_NewRef(Py_False) : Py_NewRef(Py_True);")
            elif op == "-":
                self.emit(t + " = PyNumber_Negative(" + operand + ");")
                self.emit("if (" + t + " == NULL) goto cratix_error;")
            elif op == "+":
                self.emit(t + " = PyNumber_Positive(" + operand + ");")
                self.emit("if (" + t + " == NULL) goto cratix_error;")
            elif op == "~":
                self.emit(t + " = PyNumber_Invert(" + operand + ");")
                self.emit("if (" + t + " == NULL) goto cratix_error;")
            else:
                raise Unsupported("一元运算符：" + str(op))
            return t
        if kind == "compare":
            self.emit_compare(node, t)
            return t
        if kind == "boolop":
            self.emit_boolop(node, t)
            return t
        if kind == "call":
            if self.emit_fast_builtin(node, t):
                return t
            if self.emit_method_call(node, t):
                return t
            self.emit_call(node, t)
            return t
        if kind == "ifexp":
            test = self.emit_expr(node.kids[0])
            self.emit("if (cratix_truth(" + test + ")) {")
            body_var = self.emit_expr(node.kids[1])
            self.emit("} else {")
            else_var = self.emit_expr(node.kids[2])
            self.emit("}")
            self.emit("if (" + body_var + " == NULL && " + else_var + " == NULL) goto cratix_error;")
            self.emit("if (" + body_var + " != NULL) { " + t + " = " + body_var + "; " + body_var + " = NULL; }")
            self.emit("else { " + t + " = " + else_var + "; " + else_var + " = NULL; }")
            return t
        if kind in ("list", "tuple"):
            self.emit_sequence(node, t, kind)
            return t
        if kind == "set":
            self.emit_set(node, t)
            return t
        if kind == "dict":
            self.emit_dict(node, t)
            return t
        if kind == "slice":
            lower = self.emit_expr(node.kids[0]) if node.kids[0] is not None else "Py_None"
            upper = self.emit_expr(node.kids[1]) if node.kids[1] is not None else "Py_None"
            step = self.emit_expr(node.kids[2]) if node.kids[2] is not None else "Py_None"
            self.emit(t + " = PySlice_New(" + lower + ", " + upper + ", " + step + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return t
        if kind == "joinedstr":
            self.emit_joinedstr(node, t)
            return t
        if kind == "formattedvalue":
            self.emit_formatted(node, t)
            return t
        if kind == "starred":
            return self.emit_expr(node.kids[0])
        if kind in ("listcomp", "setcomp", "dictcomp"):
            self.emit_comprehension(node, t, kind)
            return t
        if kind == "lambda":
            # **lambda 的值 = 那个原生函数对象** ✅（模块级 ✅，从模块字典里取 ✅）
            name = self.lambda_names.get(node.get("key"))
            if not name:
                raise Unsupported("lambda（未登记）")
            fn = self.new_tmp()
            self.emit(fn + " = PyDict_GetItemString(cratix_globals, " + c_string(name) + ");")
            self.emit("if (" + fn + " == NULL) {")
            self.emit("    PyErr_SetString(PyExc_NameError, \"cratix: lambda helper missing\");")
            self.emit("    goto cratix_error;")
            self.emit("}")
            captures = set((self.lambda_captures or {}).get(node.get("key")) or ())
            if captures:
                # **捕获外层局部** ✅ -> `functools.partial(fn, cells)` ✅（与嵌套 def 一致 ✅）
                if not self.cells_var:
                    raise Unsupported("lambda 捕获了变量但没有 cell 字典")
                fmod = self.new_tmp()
                part = self.new_tmp()
                self.emit(fmod + " = PyImport_ImportModule(\"functools\");")
                self.emit("if (" + fmod + " == NULL) goto cratix_error;")
                self.emit(part + " = PyObject_GetAttrString(" + fmod + ", \"partial\");")
                self.emit("Py_DECREF(" + fmod + ");")
                self.emit(fmod + " = NULL;")
                self.emit("if (" + part + " == NULL) goto cratix_error;")
                self.emit(t + " = PyObject_CallFunctionObjArgs(" + part + ", " + fn + ", " + self.cells_var + ", NULL);")
                self.emit("Py_DECREF(" + part + ");")
                self.emit(part + " = NULL;")
                self.emit("if (" + t + " == NULL) goto cratix_error;")
                return t
            self.emit(t + " = Py_NewRef(" + fn + ");")
            return t
        if kind == "genexp":
            self.emit_genexp(node, t)
            return t
        raise Unsupported("表达式：" + kind)
    def emit_compare(self, node, t) -> None:
        """比较链 ✅（短路 ✅ —— 中间的值只求值一次 ✅）。"""
        ops = node.get("ops", [])
        cur = self.emit_expr(node.kids[0])
        failed = self.new_int_helper()
        self.emit(failed + " = 0;")
        for i, op in enumerate(ops):
            right = self.emit_expr(node.kids[i + 1])
            self.emit("if (!" + failed + ") {")
            if op in ("is", "is not"):
                # **指针比较** ✅ —— "is" 成立条件是**相等** ✅；
                # "is not" 成立条件是**不等** ✅（旧口径极性反了 ✅）。
                if op == "is":
                    self.emit("    if (" + cur + " != " + right + ") " + failed + " = 1;")
                else:
                    self.emit("    if (" + cur + " == " + right + ") " + failed + " = 1;")
            elif op in ("in", "not in"):
                r = self.new_int_helper()
                self.emit("    " + r + " = PySequence_Contains(" + right + ", " + cur + ");")
                self.emit("    if (" + r + " < 0) goto cratix_error;")
                neg = "!" if op == "not in" else ""
                self.emit("    if (" + neg + r + ") " + failed + " = 1;")
            else:
                code = CMP_CODES.get(op)
                if code is None:
                    raise Unsupported("比较运算符：" + str(op))
                r = self.new_int_helper()
                self.emit("    " + r + " = PyObject_RichCompareBool(" + cur + ", " + right + ", " + code + ");")
                self.emit("    if (" + r + " < 0) goto cratix_error;")
                self.emit("    if (!" + r + ") " + failed + " = 1;")
            self.emit("}")
            cur = right
        self.emit(t + " = " + failed + " ? Py_NewRef(Py_False) : Py_NewRef(Py_True);")

    def emit_boolop(self, node, t) -> None:
        """and / or ✅（短路 ✅ —— 返回决定结果的那个值 ✅）。"""
        op = node.get("op")
        cur = self.emit_expr(node.kids[0])
        for value in node.kids[1:]:
            nxt = self.new_tmp()
            if op == "and":
                cond = "cratix_truth(" + cur + ")"
            else:
                cond = "!cratix_truth(" + cur + ")"
            self.emit("if (" + cond + ") {")
            nxt = self.emit_expr(value)
            self.emit("}")
            self.emit("if (" + nxt + " != NULL) {")
            self.emit("    Py_XDECREF(" + cur + ");")
            self.emit("    " + cur + " = " + nxt + ";")
            self.emit("    " + nxt + " = NULL;")
            self.emit("}")
        self.emit(t + " = " + cur + ";")
        self.emit(cur + " = NULL;")

    def emit_builtin_method_fast(self, node, t) -> bool:
        """**内建类型的常见方法直译** ✅ —— 省掉属性查找 + 绑定方法 ✅。

        只在**类型精确匹配**时才走快路径 ✅（否则退回通用 VectorcallMethod ✅ —— 永远安全 ✅）。
        """
        target = node.kids[0]
        if target.kind != "attr" or node.get("keywords"):
            return False
        attr = target.get("attr")
        positional = node.kids[1:]
        receiver = target.kids[0]
        if attr == "append" and len(positional) == 1:
            self.emit_attr_name(target)
            obj = self.emit_expr(receiver)
            value = self.emit_expr(positional[0])
            self.emit("if (PyList_CheckExact(" + obj + ")) {")
            self.emit("    if (PyList_Append(" + obj + ", " + value + ") < 0) goto cratix_error;")
            self.emit("    " + t + " = Py_NewRef(Py_None);")
            self.emit("} else {")
            self.emit("    PyObject *cratix_am = PyObject_GetAttr(" + obj + ", " + self.attr_ref(target) + ");")
            self.emit("    if (cratix_am == NULL) goto cratix_error;")
            self.emit("    " + t + " = PyObject_CallOneArg(cratix_am, " + value + ");")
            self.emit("    Py_DECREF(cratix_am);")
            self.emit("    if (" + t + " == NULL) goto cratix_error;")
            self.emit("}")
            return True
        if attr == "get" and len(positional) == 1:
            self.emit_attr_name(target)
            obj = self.emit_expr(receiver)
            key = self.emit_expr(positional[0])
            self.emit("if (PyDict_CheckExact(" + obj + ")) {")
            self.emit("    " + t + " = PyDict_GetItemWithError(" + obj + ", " + key + ");")
            self.emit("    if (" + t + " == NULL && PyErr_Occurred()) goto cratix_error;")
            self.emit("    if (" + t + " == NULL) { " + t + " = Py_NewRef(Py_None); } else { Py_INCREF(" + t + "); }")
            self.emit("} else {")
            self.emit("    PyObject *cratix_gm = PyObject_GetAttr(" + obj + ", " + self.attr_ref(target) + ");")
            self.emit("    if (cratix_gm == NULL) goto cratix_error;")
            self.emit("    " + t + " = PyObject_CallOneArg(cratix_gm, " + key + ");")
            self.emit("    Py_DECREF(cratix_gm);")
            self.emit("    if (" + t + " == NULL) goto cratix_error;")
            self.emit("}")
            return True
        return False

    def emit_method_call(self, node, t) -> bool:
        """方法调用 ✅ —— 走 `PyObject_VectorcallMethod` ✅。

        **这是 CPython 3.14 解释器的 LOAD_METHOD 专门化路径** ✅ ——
        它绕过**属性查找** ✅（直接用类型的方法槽 ✅ ✅）—— 通用路径最大的开销 ✅ ✅。
        """
        target = node.kids[0]
        if target.kind != "attr":
            return False
        positional = []
        keywords = []
        for child in node.kids[1:]:
            if child.kind == "starred":
                return False
            positional.append(child)
        for name, child in node.get("keywords", []):
            if name is None:
                return False
            keywords.append((name, child))
        n_pos = len(positional)
        n_kw = len(keywords)
        n = n_pos + n_kw
        if n + 1 > MAX_VECTORCALL_ARGS:
            return False
        # **内建类型的常见方法** ✅（list.append / dict.get ✅ —— 类型对得上才走快路径 ✅）
        if self.emit_builtin_method_fast(node, t):
            return True
        # **直调本类方法** ✅ —— `self.foo(...)` ✅、foo 已 AOT ✅、且没被实例属性覆盖 ✅、无关键字/解包 ✅
        direct = self.class_methods.get(target.get("attr"))
        if (
            direct
            and target.kids[0].kind == "name"
            and target.kids[0].get("id") == "self"
            and target.get("attr") not in self.self_attr_writes
            and not keywords
        ):
            receiver = self.emit_expr(target.kids[0])
            arg_vals = [self.emit_expr(child) for child in positional]
            # **方法描述符的原生函数** ✅ 只收"非 self"参数 ✅（self 走 C 的 self ✅）
            args_tuple = self.new_tmp()
            self.emit(args_tuple + " = PyTuple_New(" + str(len(arg_vals)) + ");")
            self.emit("if (" + args_tuple + " == NULL) goto cratix_error;")
            for index, value in enumerate(arg_vals):
                self.emit("Py_INCREF(" + value + ");")
                self.emit("PyTuple_SET_ITEM(" + args_tuple + ", " + str(index) + ", " + value + ");")
            self.emit(t + " = " + direct + "(" + receiver + ", " + args_tuple + ", NULL);")
            self.emit("Py_DECREF(" + args_tuple + ");")
            self.emit(args_tuple + " = NULL;")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
            return True
        obj = self.emit_expr(target.kids[0])
        # **先把所有参数求值完** ✅ 再填 vargs ✅ ——
        # vargs 是**函数级共享**的 ✅；参数表达式里若再发生方法调用 ✅ -> 会覆盖已填好的槽位 ✅
        # （实测：`"-".join([w.upper() for w in words])` 用错接收者 ✅ -> 结果错 ✅）。
        arg_vals = [self.emit_expr(child) for child in positional]
        kw_vals = [(name, self.emit_expr(child)) for name, child in keywords]
        # **vargs[1] 是对象** ✅（VectorcallMethod 的约定 ✅ —— 第 0 格留给草稿 ✅）
        # **不要 INCREF** ✅：vectorcall 只**借用**参数 ✅ ——
        # 原来 INCREF 进 vargs 却从不释放 ✅ -> 每次带参数的调用泄漏一个引用 ✅（实测 200 轮 +200 ✅）；
        # 临时变量本来就持有引用 ✅ -> 由函数/循环末尾的清理统一释放 ✅。
        self.emit("cratix_vargs[1] = " + obj + ";")
        for i, v in enumerate(arg_vals):
            self.emit("cratix_vargs[" + str(i + 2) + "] = " + v + ";")
        if n_kw:
            self.emit("cratix_kwnames = PyTuple_New(" + str(n_kw) + ");")
            self.emit("if (cratix_kwnames == NULL) goto cratix_error;")
            for j, (name, v) in enumerate(kw_vals):
                self.emit("cratix_vargs[" + str(n_pos + 2 + j) + "] = " + v + ";")
                self.emit("PyObject *cratix_kn = PyUnicode_FromString(" + c_string(name) + ");")
                self.emit("if (cratix_kn == NULL) goto cratix_error;")
                self.emit("PyTuple_SET_ITEM(cratix_kwnames, " + str(j) + ", cratix_kn);")
        else:
            self.emit("cratix_kwnames = NULL;")
        # **方法名走常量池** ✅ —— 原来**每次调用**都 `PyUnicode_FromString` ✅（真分配 ✅）
        mname = self.emit_const_piece(target.get("attr"))
        self.emit(t + " = PyObject_VectorcallMethod(" + mname + ", cratix_vargs + 1, " + str(n + 1) + " | PY_VECTORCALL_ARGUMENTS_OFFSET, cratix_kwnames);")
        self.emit("Py_DECREF(" + mname + ");")
        self.emit(mname + " = NULL;")
        if n_kw:
            self.emit("Py_DECREF(cratix_kwnames);")
            self.emit("cratix_kwnames = NULL;")
        # **VectorcallMethod 会取走参数的引用** ✅ —— 不能 DECREF ✅ ✅
        self.emit("if (" + t + " == NULL) goto cratix_error;")
        return True

    def emit_call(self, node, t) -> None:
        """调用 ✅（位置 + 关键字 + 星号展开 ✅）。

        **向量调用** ✅（PyObject_Vectorcall ✅）—— 省掉每次调用的元组分配 ✅ ✅
        （CPython 3.14 的解释器也是这么做的 ✅ ✅）。
        """
        func = self.emit_expr(node.kids[0])
        positional = []
        keywords = []
        has_star = False
        for child in node.kids[1:]:
            if child.kind == "starred":
                has_star = True
                break
            positional.append(child)
        if not has_star:
            for name, child in node.get("keywords", []):
                if name is None:
                    has_star = True
                    break
                keywords.append((name, child))
        if not has_star:
            self.emit_vectorcall(node, func, positional, keywords, t)
            return
        self.emit_call_generic(node, func, t)

    def emit_vectorcall(self, node, func, positional, keywords, t) -> None:
        """向量调用 ✅ —— 参数放在**连续**的栈数组里 ✅（vectorcall 要求 ✅）。"""
        n_pos = len(positional)
        n_kw = len(keywords)
        n = n_pos + n_kw
        # **参数多于预留槽位** ✅ -> 退回通用路径 ✅（避免越界 ✅ ✅）
        if n > MAX_VECTORCALL_ARGS:
            self.emit_call_generic(node, func, t)
            return
        # **先把所有参数求值完** ✅ 再填 vargs ✅ —— vargs 是函数级共享的 ✅，
        # 参数里的嵌套调用会覆盖已填好的槽位 ✅（与 emit_method_call 同一个坑 ✅）。
        arg_vals = [self.emit_expr(child) for child in positional]
        kw_vals = [(name, self.emit_expr(child)) for name, child in keywords]
        # **借用语义** ✅：vectorcall 不取走参数 ✅ -> 不 INCREF ✅（临时变量负责持有 ✅）
        for i, v in enumerate(arg_vals):
            self.emit("cratix_vargs[" + str(i + 1) + "] = " + v + ";")
        if n_kw:
            self.emit("cratix_kwnames = PyTuple_New(" + str(n_kw) + ");")
            self.emit("if (cratix_kwnames == NULL) goto cratix_error;")
            for j, (name, v) in enumerate(kw_vals):
                self.emit("cratix_vargs[" + str(n_pos + j + 1) + "] = " + v + ";")
                self.emit("PyObject *cratix_kn = PyUnicode_FromString(" + c_string(name) + ");")
                self.emit("if (cratix_kn == NULL) goto cratix_error;")
                self.emit("PyTuple_SET_ITEM(cratix_kwnames, " + str(j) + ", cratix_kn);")
        else:
            self.emit("cratix_kwnames = NULL;")
        # **nargsf 只算"位置参数"** ✅ —— 关键字的值排在后面 ✅、个数由 kwnames 决定 ✅。
        # 传成总个数 ✅ -> CPython 以为多了一个位置参数 ✅ -> "sorted expected 1 argument, got 2" ✅（实测 ✅）
        self.emit(t + " = PyObject_Vectorcall(" + func + ", cratix_vargs + 1, " + str(n_pos) + ", cratix_kwnames);")
        if n_kw:
            self.emit("Py_DECREF(cratix_kwnames);")
            self.emit("cratix_kwnames = NULL;")
        # **vectorcall 会取走参数的引用** ✅ —— 这里不能 DECREF ✅（否则双重释放 ✅）
        self.emit("if (" + t + " == NULL) goto cratix_error;")

    def emit_call_generic(self, node, func, t) -> None:
        """通用调用 ✅（带星号展开 ✅）—— 走元组 ✅ ✅。"""
        args_list = self.new_tmp()
        self.emit(args_list + " = PyList_New(0);")
        self.emit("if (" + args_list + " == NULL) goto cratix_error;")
        for child in node.kids[1:]:
            if child.kind == "starred":
                it = self.new_helper()
                src = self.emit_expr(child.kids[0])
                self.emit(it + " = PyObject_GetIter(" + src + ");")
                self.emit("if (" + it + " == NULL) goto cratix_error;")
                self.emit("while (1) {")
                item = self.new_tmp()
                self.emit("    " + item + " = PyIter_Next(" + it + ");")
                self.emit("    if (" + item + " == NULL) { if (PyErr_Occurred()) goto cratix_error; break; }")
                self.emit("    if (PyList_Append(" + args_list + ", " + item + ") < 0) goto cratix_error;")
                self.emit("}")
                self.emit("Py_DECREF(" + it + ");")
                self.emit(it + " = NULL;")
            else:
                item = self.emit_expr(child)
                self.emit("if (PyList_Append(" + args_list + ", " + item + ") < 0) goto cratix_error;")
        argtuple = self.new_tmp()
        self.emit(argtuple + " = PyList_AsTuple(" + args_list + ");")
        self.emit("if (" + argtuple + " == NULL) goto cratix_error;")
        keywords = node.get("keywords", [])
        if keywords:
            kwdict = self.new_tmp()
            self.emit(kwdict + " = PyDict_New();")
            self.emit("if (" + kwdict + " == NULL) goto cratix_error;")
            for name, child in keywords:
                if name is None:
                    src = self.emit_expr(child.kids[0])
                    self.emit("if (PyDict_Update(" + kwdict + ", " + src + ") < 0) goto cratix_error;")
                else:
                    val = self.emit_expr(child)
                    self.emit("if (PyDict_SetItemString(" + kwdict + ", " + c_string(name) + ", " + val + ") < 0) goto cratix_error;")
            self.emit(t + " = PyObject_Call(" + func + ", " + argtuple + ", " + kwdict + ");")
            self.emit("Py_DECREF(" + kwdict + ");")
            self.emit(kwdict + " = NULL;")
        else:
            self.emit(t + " = PyObject_Call(" + func + ", " + argtuple + ", NULL);")
        self.emit("if (" + t + " == NULL) goto cratix_error;")

    def emit_sequence(self, node, t, kind) -> None:
        items = [self.emit_expr(k) for k in node.kids]
        ctor = "PyTuple_New" if kind == "tuple" else "PyList_New"
        setter = "PyTuple_SET_ITEM" if kind == "tuple" else "PyList_SET_ITEM"
        self.emit(t + " = " + ctor + "(" + str(len(items)) + ");")
        self.emit("if (" + t + " == NULL) goto cratix_error;")
        for i, item in enumerate(items):
            self.emit(setter + "(" + t + ", " + str(i) + ", " + item + ");")
            self.emit(item + " = NULL;")

    def emit_set(self, node, t) -> None:
        self.emit(t + " = PySet_New(NULL);")
        self.emit("if (" + t + " == NULL) goto cratix_error;")
        for kid in node.kids:
            item = self.emit_expr(kid)
            self.emit("if (PySet_Add(" + t + ", " + item + ") < 0) goto cratix_error;")

    def emit_dict(self, node, t) -> None:
        self.emit(t + " = PyDict_New();")
        self.emit("if (" + t + " == NULL) goto cratix_error;")
        for i in range(0, len(node.kids), 2):
            key = node.kids[i]
            value = node.kids[i + 1]
            if key is None:
                src = self.emit_expr(value)
                self.emit("if (PyDict_Update(" + t + ", " + src + ") < 0) goto cratix_error;")
            else:
                k = self.emit_expr(key)
                v = self.emit_expr(value)
                self.emit("if (PyDict_SetItem(" + t + ", " + k + ", " + v + ") < 0) goto cratix_error;")

    def emit_const_piece(self, value) -> str:
        """常量字符串 -> 池化引用 ✅（新引用 ✅）。"""
        piece = self.new_tmp()
        if self.use_pool:
            name = self.consts.ref(value)
            self.emit(piece + " = " + name + ";")
            self.emit("Py_INCREF(" + piece + ");")
        else:
            self.emit(piece + " = " + const_expr(value) + ";")
            self.emit("if (" + piece + " == NULL) goto cratix_error;")
        return piece

    def emit_joinedstr(self, node, t) -> None:
        """f-string ✅。

        **优化** ✅：
        - 常量片段走**常量池** ✅（原来每次 `PyUnicode_FromString` 真分配 ✅）；
        - 只有一段时**不建列表** ✅（直接转移 ✅）；
        - 多段用 `PyList_New(n)` + `SET_ITEM` ✅（省掉 n 次 Append ✅）。
        """
        kids = list(node.kids)
        if not kids:
            self.emit(t + " = cratix_empty_str();")
            return
        if len(kids) == 1:
            child = kids[0]
            if child.kind == "const":
                piece = self.emit_const_piece(child.get("value"))
            else:
                piece = self.emit_expr(child)
            self.emit(t + " = " + piece + ";")
            self.emit(piece + " = NULL;")
            return
        parts = self.new_tmp()
        self.emit(parts + " = PyList_New(" + str(len(kids)) + ");")
        self.emit("if (" + parts + " == NULL) goto cratix_error;")
        for i, child in enumerate(kids):
            if child.kind == "const":
                piece = self.emit_const_piece(child.get("value"))
            else:
                piece = self.emit_expr(child)
            # **SET_ITEM 取走引用** ✅ —— 所以先置 NULL ✅ 免得清理时二次释放 ✅
            self.emit("PyList_SET_ITEM(" + parts + ", " + str(i) + ", " + piece + ");")
            self.emit(piece + " = NULL;")
        sep = self.emit_const_piece("")
        self.emit(t + " = PyUnicode_Join(" + sep + ", " + parts + ");")
        self.emit("if (" + t + " == NULL) goto cratix_error;")

    def emit_formatted(self, node, t) -> None:
        value = self.emit_expr(node.kids[0])
        conv = node.get("conversion", -1)
        if conv == 114:
            base = self.new_tmp()
            self.emit(base + " = PyObject_Repr(" + value + ");")
            self.emit("if (" + base + " == NULL) goto cratix_error;")
        elif conv == 115:
            base = self.new_tmp()
            self.emit(base + " = PyObject_Str(" + value + ");")
            self.emit("if (" + base + " == NULL) goto cratix_error;")
        elif conv == 97:
            base = self.new_tmp()
            self.emit(base + " = PyObject_ASCII(" + value + ");")
            self.emit("if (" + base + " == NULL) goto cratix_error;")
        else:
            base = value
        spec = node.get("format_spec")
        if spec is None:
            if conv == -1:
                # 无转换无格式 ✅ -> **`format(value, "")`** ✅（走 `__format__` ✅ —— 与 CPython 一致 ✅；
                # 用 `str()` 会漏掉自定义 `__format__` ✅）
                self.emit(t + " = PyObject_Format(" + base + ", NULL);")
            else:
                # `!r`/`!s`/`!a` 的结果已经是 str ✅ -> 空格式就是它本身 ✅（省一次 format ✅）
                self.emit(t + " = " + base + ";")
                self.emit(base + " = NULL;")
        elif spec.kind == "const" and isinstance(spec.get("value"), str):
            # **静态格式串** ✅（`:03d` 之类最常见 ✅）—— 直接走常量池 ✅，不建列表 ✅
            spec_piece = self.emit_const_piece(spec.get("value"))
            self.emit(t + " = cratix_format(" + base + ", " + spec_piece + ");")
        else:
            spec_str = self.emit_expr(spec)
            self.emit(t + " = cratix_format(" + base + ", " + spec_str + ");")
        self.emit("if (" + t + " == NULL) goto cratix_error;")

    def _genexp_names(self, node) -> set:
        """收集生成器表达式里**读到的名字** ✅（不含它自己的目标 ✅）。"""
        out = set()

        def walk(ir_node):
            if ir_node is None or not hasattr(ir_node, "kind"):
                return
            if ir_node.kind == "name":
                out.add(ir_node.get("id"))
                return
            for kid in ir_node.kids:
                if isinstance(kid, tuple):
                    for item in kid:
                        if isinstance(item, list):
                            for sub in item:
                                walk(sub)
                        else:
                            walk(item)
                else:
                    walk(kid)

        walk(node.kids[0])
        targets = set()
        gens = node.kids[1:]
        # **最外层的可迭代对象在外层作用域求值** ✅ -> 它里面的名字不算"捕获" ✅
        for index, (target, iterable, ifs) in enumerate(gens):
            if index > 0:
                walk(iterable)
            for cond in ifs:
                walk(cond)
        for target, _iterable, _ifs in node.kids[1:]:
            names = []
            self._comp_target_names(target, names)
            targets.update(names)
        return out - targets

    def emit_genexp(self, node, t) -> None:
        """生成器表达式 ✅ —— **真惰性** ✅（产出一个真生成器对象 ✅）。

        **为什么用"合成源码"** ✅：C 层要自己实现生成器的状态机 ✅（暂停/恢复 ✅）工作量巨大 ✅；
        而 CPython 本来就支持生成器函数 ✅ —— 我们在**模块 init 里建出这个函数** ✅
        （`PyRun_String` ✅），调用点只把**最外层可迭代对象**传进去 ✅。
        Python 语义 ✅：最外层可迭代对象**立即求值** ✅，其余部分**惰性** ✅ —— 与真生成器表达式一致 ✅。
        """
        gens = node.kids[1:]
        if not gens:
            raise Unsupported("空生成器表达式")
        # **引用外层局部变量** ✅ -> 合不出来 ✅（合成函数是模块级的 ✅ 看不见函数局部 ✅）-> 整函数回退 ✅
        # **捕获外层局部** ✅ -> 合成函数多一个 `_cratix_cells` 首参 ✅（用 partial 绑定 ✅）
        captured = set(self.genexp_captures.get(node.get("key")) or ())
        text = node.get("text") or {}
        elt_src = text.get("elt")
        gen_texts = text.get("gens") or []
        if not elt_src or len(gen_texts) != len(gens):
            raise Unsupported("生成器表达式缺少源码片段")
        name = "cratix_gen_" + c_ident(self.func_name)
        index = 0
        while name in self.genexp_sources:
            index += 1
            name = "cratix_gen_" + c_ident(self.func_name) + "_" + str(index)
        self.genexp_sources[name] = genexp_source(name, elt_src, gen_texts, captured)
        # **最外层可迭代对象立即求值** ✅
        first_iter = self.emit_expr(gens[0][1])
        helper = self.new_tmp()
        self.emit(helper + " = PyDict_GetItemString(cratix_globals, " + c_string(name) + ");")
        self.emit("if (" + helper + " == NULL) {")
        self.emit("    PyErr_SetString(PyExc_NameError, \"cratix: generator helper missing\");")
        self.emit("    goto cratix_error;")
        self.emit("}")
        if captured:
            if not self.cells_var:
                raise Unsupported("生成器表达式捕获了变量但没有 cell 字典")
            fmod = self.new_tmp()
            part = self.new_tmp()
            self.emit(fmod + " = PyImport_ImportModule(\"functools\");")
            self.emit("if (" + fmod + " == NULL) goto cratix_error;")
            self.emit(part + " = PyObject_GetAttrString(" + fmod + ", \"partial\");")
            self.emit("Py_DECREF(" + fmod + ");")
            self.emit(fmod + " = NULL;")
            self.emit("if (" + part + " == NULL) goto cratix_error;")
            self.emit(helper + " = PyObject_CallFunctionObjArgs(" + part + ", " + helper + ", " + self.cells_var + ", NULL);")
            self.emit("Py_DECREF(" + part + ");")
            self.emit(part + " = NULL;")
            self.emit("if (" + helper + " == NULL) goto cratix_error;")
        self.emit(t + " = PyObject_CallOneArg(" + helper + ", " + first_iter + ");")
        self.emit("if (" + t + " == NULL) goto cratix_error;")

    def comp_var(self, name: str):
        """推导式作用域里的名字 -> C 临时变量 ✅（由内向外找 ✅）。"""
        for scope in reversed(self.comp_scopes):
            if name in scope:
                return scope[name]
        return None

    def _comp_target_names(self, target, acc) -> None:
        """收集推导式目标里的名字 ✅（含元组解包 ✅）。"""
        if target is None:
            return
        if target.kind == "name":
            acc.append(target.get("id"))
            return
        if target.kind == "starred":
            self._comp_target_names(target.kids[0], acc)
            return
        for kid in target.kids:
            self._comp_target_names(kid, acc)

    def emit_comprehension(self, node, t, kind) -> None:
        """推导式 ✅ —— 支持**多生成器** / **元组解包** / **嵌套** ✅。

        **作用域** ✅：Python 3 的推导式有独立作用域 ✅ -> 目标名绑到**专用临时变量** ✅，
        读名字时由内向外查（见 comp_var ✅）—— 既不污染函数局部 ✅，
        也不会出现"元素表达式读到未赋值的局部变量" ✅（那会直接 SystemError ✅ 实测 ✅）。
        """
        kids = list(node.kids)
        offset = 2 if kind == "dictcomp" else 1
        gens = kids[offset:]
        if not gens:
            raise Unsupported("空推导式")
        names: list = []
        for target, _iterable, _ifs in gens:
            self._comp_target_names(target, names)
        if not names:
            raise Unsupported("推导式目标里没有名字")
        scope = {}
        for name in names:
            if name not in scope:
                scope[name] = self.new_tmp()
        # **最外层的可迭代对象在"外层作用域"求值** ✅ ——
        # `[x for x in x]` 里的第一个 x 是外层变量 ✅（先压作用域会读错 ✅）。
        self.emit("/* --- 推导式（独立作用域 ✅）--- */")
        src = self.emit_expr(gens[0][1])
        self.comp_scopes.append(scope)
        first_it = self.new_helper()
        self.emit(first_it + " = PyObject_GetIter(" + src + ");")
        self.emit("if (" + first_it + " == NULL) goto cratix_error;")
        if kind == "dictcomp":
            self.emit(t + " = PyDict_New();")
        elif kind == "setcomp":
            self.emit(t + " = PySet_New(NULL);")
        else:
            self.emit(t + " = PyList_New(0);")
        self.emit("if (" + t + " == NULL) goto cratix_error;")
        tmp_mark = self.tmp_index
        self.emit_comp_level(gens, 0, t, kind, kids, first_it)
        # **推导式内部的临时变量用完即放** ✅（否则每次执行泄漏 ✅）
        self.emit_tmp_cleanup(tmp_mark)
        self.comp_scopes.pop()

    def emit_comp_level(self, gens, index, t, kind, kids, iterator: str) -> None:
        """一层生成器 ✅ —— 多生成器就是**嵌套循环** ✅（后面的可以引用前面的目标 ✅）。"""
        target, _iterable, ifs = gens[index]
        # **本轮迭代的临时变量起点** ✅ —— 元素/条件的临时变量要**每轮释放** ✅
        # （函数级复用 ✅ -> 不释放就是每轮泄漏一个引用 ✅ 实测 ✅）
        mark = self.tmp_index
        self.emit("while (1) {")
        item = self.new_tmp()
        self.emit("    " + item + " = PyIter_Next(" + iterator + ");")
        self.emit("    if (" + item + " == NULL) { if (PyErr_Occurred()) goto cratix_error; break; }")
        self.emit_assign(target, item)
        for cond in ifs:
            c = self.emit_expr(cond)
            self.emit("    if (!cratix_truth(" + c + ")) {")
            self.emit_tmp_cleanup(mark, "        ")
            self.emit("        continue;")
            self.emit("    }")
        if index + 1 < len(gens):
            src = self.emit_expr(gens[index + 1][1])
            inner = self.new_helper()
            self.emit("    " + inner + " = PyObject_GetIter(" + src + ");")
            self.emit("    if (" + inner + " == NULL) goto cratix_error;")
            self.emit_comp_level(gens, index + 1, t, kind, kids, inner)
            self.emit("    Py_DECREF(" + inner + ");")
            self.emit("    " + inner + " = NULL;")
        elif kind == "dictcomp":
            k = self.emit_expr(kids[0])
            v = self.emit_expr(kids[1])
            self.emit("        if (PyDict_SetItem(" + t + ", " + k + ", " + v + ") < 0) goto cratix_error;")
        else:
            elt = self.emit_expr(kids[0])
            if kind == "setcomp":
                self.emit("        if (PySet_Add(" + t + ", " + elt + ") < 0) goto cratix_error;")
            else:
                self.emit("        if (PyList_Append(" + t + ", " + elt + ") < 0) goto cratix_error;")
        self.emit_tmp_cleanup(mark, "    ")
        self.emit("}")
        if index == 0:
            self.emit("Py_DECREF(" + iterator + ");")
            self.emit(iterator + " = NULL;")

def target_attr(node) -> str:
    return node.get("attr")


def c_bytes_literal(data: bytes) -> str:
    """bytes 字面量 ✅（逐字节转义 ✅ —— 避免十六进制转义后面跟十六进制字符的歧义 ✅）。"""
    out = ["\""]
    for byte in data:
        out.append("\\x%02x" % byte)
    out.append("\"")
    return "".join(out)