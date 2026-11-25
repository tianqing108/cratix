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

from ..frontend.subset import Unsupported


# 向量调用的参数槽位上限 ✅（超出走通用路径 ✅ ✅）。
MAX_VECTORCALL_ARGS = 16

# try/except 的实现上限 ✅（嵌套深度 ✅ / 每层 handler 数 ✅）。
MAX_TRY_DEPTH = 8
MAX_TRY_HANDLERS = 4


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
    decls.append("    PyObject *cratix_vargs[" + str(MAX_VECTORCALL_ARGS) + "];")
    decls.append("    PyObject *cratix_kwnames = NULL;")
    decls.append("    PyObject *cratix_mname = NULL;")
    # **try 栈** ✅（每个函数一份 ✅ —— try/except 的实现状态 ✅ ✅）
    decls.append("    int cratix_try_n = 0;")
    decls.append("    int cratix_try_idx[" + str(MAX_TRY_DEPTH) + "];")
    decls.append("    PyObject *cratix_try_types[" + str(MAX_TRY_DEPTH) + "][" + str(MAX_TRY_HANDLERS) + "];")
    cleanup = []
    # **cratix_kwv 是借用引用** ✅（PyDict_GetItemWithError ✅）—— **不能 DECREF** ✅
    # （多 DECREF 一次 ✅ → 提前释放 ✅ → 后续 use-after-free ✅ → 访问违例 ✅）
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
    lines.append("    goto cratix_done;")
    lines.append("cratix_error:")
    lines.extend(gen.emit_try_handlers())
    lines.append("    result = NULL;")
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
    decls.append("    PyObject *cratix_vargs[" + str(MAX_VECTORCALL_ARGS) + "];")
    decls.append("    PyObject *cratix_kwnames = NULL;")
    decls.append("    PyObject *cratix_mname = NULL;")
    # **try 栈** ✅（每个函数一份 ✅ —— try/except 的实现状态 ✅ ✅）
    decls.append("    int cratix_try_n = 0;")
    decls.append("    int cratix_try_idx[" + str(MAX_TRY_DEPTH) + "];")
    decls.append("    PyObject *cratix_try_types[" + str(MAX_TRY_DEPTH) + "][" + str(MAX_TRY_HANDLERS) + "];")
    cleanup = []
    for name in gen.helper_names:
        cleanup.append("    Py_XDECREF(" + name + ");")
    for name in gen.tmp_names:
        cleanup.append("    Py_XDECREF(" + name + ");")
    return decls, list(gen.lines), cleanup




def generate_module_single_phase(module_name: str, init_symbol: str, functions: list,
                                 key_names: list, module_gen=None, alias_init: str | None = None,
                                 fallback_code: dict | None = None, classes: list | None = None) -> str:
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
        out.append("    {" + q + gen.func_name + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, NULL},")
    out.append("    {NULL, NULL, 0, NULL}")
    out.append("};")
    out.append("")
    # --- 模块体 ✅（在 init 里执行一次 ✅）---
    out.append("static int cratix_run_body(PyObject *m) {")
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
    for key in sorted(set(key_names)):
        out.append("    cratix_key_" + c_ident(key) + " = PyUnicode_InternFromString(" + q + key + q + ");")
        out.append("    if (cratix_key_" + c_ident(key) + " == NULL) return -1;")
    if module_gen is not None:
        out.extend(body_lines)
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
        # **tp_new** ✅：分配实例 ✅ 并调用 __init__ ✅（把 self 放第一个 ✅）
        out.append("static PyObject *cratix_new_" + c_ident(cls["name"]) + "(PyTypeObject *type, PyObject *args, PyObject *kwargs) {")
        out.append("    PyObject *self = type->tp_alloc(type, 0);")
        out.append("    PyObject *init; PyObject *callargs; PyObject *res; Py_ssize_t n, i;")
        out.append("    if (self == NULL) return NULL;")
        out.append("    init = PyObject_GetAttrString((PyObject *)type, \"__init__\");")
        out.append("    if (init == NULL) { Py_DECREF(self); return NULL; }")
        out.append("    n = PyTuple_GET_SIZE(args);")
        out.append("    callargs = PyTuple_New(n + 1);")
        out.append("    if (callargs == NULL) { Py_DECREF(init); Py_DECREF(self); return NULL; }")
        out.append("    Py_INCREF(self); PyTuple_SET_ITEM(callargs, 0, self);")
        out.append("    for (i = 0; i < n; i++) { PyObject *v = PyTuple_GET_ITEM(args, i); Py_INCREF(v); PyTuple_SET_ITEM(callargs, i + 1, v); }")
        out.append("    res = PyObject_Call(init, callargs, kwargs);")
        out.append("    Py_DECREF(callargs); Py_DECREF(init);")
        out.append("    if (res == NULL) { Py_DECREF(self); return NULL; }")
        out.append("    Py_DECREF(res);")
        out.append("    return self;")
        out.append("}")
        out.append("")
        out.append("static PyType_Slot cratix_cls_" + c_ident(cls["name"]) + "_slots[] = {")
        out.append("    {Py_tp_new, (void *)cratix_new_" + c_ident(cls["name"]) + "},")
        out.append("    {0, NULL}")
        out.append("};")
        out.append("")
        out.append("static PyType_Spec cratix_cls_" + c_ident(cls["name"]) + "_spec = {")
        out.append("    " + q + module_name + "." + cls["name"] + q + ",")
        out.append("    sizeof(PyObject), 0,")
        out.append("    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_MANAGED_DICT,")
        out.append("    cratix_cls_" + c_ident(cls["name"]) + "_slots")
        out.append("};")
        out.append("")
        out.append("static PyMethodDef cratix_cls_" + c_ident(cls["name"]) + "_methods[] = {")
        for method_name, _gen in cls.get("methods", []):
            cast = "(PyCFunction)(void(*)(void))cratix_fn_" + c_ident(cls["name"] + "." + method_name)
            # **私有名带类名** ✅ —— 不同类的同名方法（如 __init__ ✅）不会冲突 ✅
            private = "_cratix_" + cls["name"] + "_" + method_name
            out.append("    {" + q + private + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, NULL},")
        out.append("    {NULL, NULL, 0, NULL}")
        out.append("};")
        out.append("")
        # **跳板源码** ✅：把 self 传给原生方法 ✅（普通 PyCFunction ✅ 不绑定 self ✅）
        for method_name, _gen in cls.get("methods", []):
            tb_name = "_cratix_tb_" + cls["name"] + "_" + method_name
            private = "_cratix_" + cls["name"] + "_" + method_name
            src = (
                "def " + tb_name + "(self, *args, **kwargs):" + chr(10)
                + "    return " + private + "(self, *args, **kwargs)" + chr(10)
            )
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
        for name in sorted(fallback_code):
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
    # --- 类 ✅：用堆类型创建 ✅（type(name, (object,), ns) ✅）---
    for cls in classes:
        table = "cratix_cls_" + c_ident(cls["name"]) + "_methods"
        out.append("    {")
        pass
        pass

        # 方法**在建类之后**挂上去 ✅ —— 3.14 的
        # `PyDescr_NewMethod(PyTypeObject *, PyMethodDef *)` ✅ 需要**类** ✅。
        # 用**方法描述符** ✅ → 调用时绑定 self ✅（普通 PyCFunction ✅ 不绑定 ✅）。
        pass


        for idx, (method_name, _gen) in enumerate(cls.get("methods", [])):
            out.append("        {")
            out.append("            PyObject *fn = PyCFunction_NewEx(&" + table + "[" + str(idx) + "], NULL, NULL);")
            out.append("            if (fn == NULL) { Py_DECREF(m); return NULL; }")
            out.append("            if (PyDict_SetItemString(cratix_globals, " + q + "_cratix_" + cls["name"] + "_" + method_name + q + ", fn) < 0) { Py_DECREF(fn); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(fn);")
            out.append("        }")
        for method_name, _gen in cls.get("methods", []):
            tb = "_cratix_tb_" + cls["name"] + "_" + method_name
            out.append("        {")
            out.append("            PyObject *t = PyRun_String(cratix_tb_" + c_ident(cls["name"] + "." + method_name) + ", Py_file_input, cratix_globals, cratix_globals);")
            out.append("            if (t == NULL) { Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(t);")
            out.append("        }")
        # **建类** ✅ —— 用 `type(name, (object,), ns)` ✅（比 PyType_FromSpec 稳 ✅ ✅）
        # 命名空间里有 **__module__** ✅ 与**跳板** ✅（普通 Python 函数 ✅ → 调用时绑定 self ✅ ✅）。
        out.append("        PyObject *ns = PyDict_New();")
        out.append("        if (ns == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        PyObject *mod_name = PyUnicode_FromString(" + q + module_name + q + ");")
        out.append("        if (mod_name == NULL) { Py_DECREF(ns); Py_DECREF(m); return NULL; }")
        out.append("        PyDict_SetItemString(ns, \"__module__\", mod_name);")
        out.append("        Py_DECREF(mod_name);")
        # **跳板进命名空间** ✅（普通 Python 函数 ✅ → 作为方法时自动绑定 self ✅ ✅）
        for method_name, _gen in cls.get("methods", []):
            tb = "_cratix_tb_" + cls["name"] + "_" + method_name
            out.append("        {")
            out.append("            PyObject *tbv = PyDict_GetItemString(cratix_globals, " + q + tb + q + ");")
            out.append("            if (tbv == NULL) { Py_DECREF(ns); Py_DECREF(m); return NULL; }")
            out.append("            Py_INCREF(tbv);")
            out.append("            PyObject *mn = PyUnicode_FromString(" + q + method_name + q + ");")
            out.append("            if (mn == NULL) { Py_DECREF(tbv); Py_DECREF(ns); Py_DECREF(m); return NULL; }")
            out.append("            PyObject_SetAttrString(tbv, \"__name__\", mn);")
            out.append("            Py_DECREF(mn);")
            out.append("            if (PyDict_SetItemString(ns, " + q + method_name + q + ", tbv) < 0) { Py_DECREF(tbv); Py_DECREF(ns); Py_DECREF(m); return NULL; }")
            out.append("            Py_DECREF(tbv);")
            out.append("        }")
        # **类属性** ✅
        for attr_name, attr_ir in cls.get("attrs", []):
            out.append("        {")
            out.append("            PyObject *av = " + const_expr(attr_ir.get("value")) + ";")
            out.append("            if (av == NULL) { Py_DECREF(ns); Py_DECREF(m); return NULL; }")
            out.append("            PyDict_SetItemString(ns, " + q + attr_name + q + ", av);")
            out.append("            Py_DECREF(av);")
            out.append("        }")
        # **建类** ✅
        out.append("        PyObject *bases = PyTuple_Pack(1, (PyObject *)&PyBaseObject_Type);")
        out.append("        if (bases == NULL) { Py_DECREF(ns); Py_DECREF(m); return NULL; }")
        out.append("        PyObject *cname = PyUnicode_FromString(" + q + cls["name"] + q + ");")
        out.append("        if (cname == NULL) { Py_DECREF(bases); Py_DECREF(ns); Py_DECREF(m); return NULL; }")
        out.append("        PyObject *cls = PyObject_CallFunctionObjArgs((PyObject *)&PyType_Type, cname, bases, ns, NULL);")
        out.append("        Py_DECREF(cname);")
        out.append("        Py_DECREF(bases);")
        out.append("        Py_DECREF(ns);")
        out.append("        if (cls == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        if (PyDict_SetItemString(cratix_globals, " + q + cls["name"] + q + ", cls) < 0) { Py_DECREF(cls); Py_DECREF(m); return NULL; }")
        out.append("        Py_DECREF(cls);")
        out.append("    }")
    # --- 函数级回退 ✅：从内嵌字节码重建函数 ✅（globals = 模块字典 ✅）---
    if fallback_code:
        out.append("    for (int i = 0; cratix_fallbacks[i].name != NULL; i++) {")
        # **在模块字典里 exec** ✅ —— 这样函数的 __globals__ 就是模块字典 ✅
        # （模块级名字才能被函数体看到 ✅）；函数会自然落到模块字典里 ✅。
        out.append("        PyObject *code = Py_CompileString(cratix_fallbacks[i].source, cratix_fallbacks[i].filename, Py_file_input);")
        out.append("        if (code == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        PyObject *res = PyEval_EvalCode(code, cratix_globals, cratix_globals);")
        out.append("        Py_DECREF(code);")
        out.append("        if (res == NULL) { Py_DECREF(m); return NULL; }")
        out.append("        Py_DECREF(res);")
        out.append("    }")
    out.append("    return m;")
    out.append("}")
    if alias_init:
        out.append("")
        out.append("PyMODINIT_FUNC " + alias_init + "(void) {")
        out.append("    return " + init_symbol + "();")
        out.append("}")
    return nl.join(out) + nl

def generate_module(module_name: str, init_symbol: str, functions: list, key_names: list,
                    module_gen=None, alias_init: str | None = None) -> str:
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
        out.append("    {" + q + gen.func_name + q + ", " + cast + ", METH_VARARGS | METH_KEYWORDS, NULL},")
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

    def __init__(self, func_name, params, body, scope):
        self.func_name = func_name
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
        n_pos = len(names)

        self.emit("/* --- 参数绑定 --- */")
        pass
        self.emit("PyObject *cratix_kw = NULL;")
        self.emit("PyObject *cratix_kwv = NULL;")
        self.emit("if (kwargs != NULL) {")
        self.emit("    cratix_kw = PyDict_Copy(kwargs);")
        self.emit("    if (cratix_kw == NULL) goto cratix_error;")
        self.emit("}")

        for i, name in enumerate(names):
            var = self.local_var(name)
            self.emit("/* " + name + " */")
            self.emit("if (" + str(i) + " < PyTuple_GET_SIZE(args)) {")
            self.emit("    " + var + " = PyTuple_GET_ITEM(args, " + str(i) + ");")
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
            self.emit("if (PyTuple_GET_SIZE(args) > " + str(n_pos) + ") {")
            self.emit("    " + var + " = PyTuple_GetSlice(args, " + str(n_pos) + ", PyTuple_GET_SIZE(args));")
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

    def attr_ref(self, node) -> str:
        """属性名的**缓存引用** ✅ —— interned 字符串 ✅（避免每次新建 ✅ ✅）。"""
        return "cratix_an_" + c_ident(target_attr(node))

    def emit_attr_name(self, node) -> None:
        """惰性初始化 interned 属性名 ✅（函数内 static ✅ ✅ —— 每个函数只声明一次 ✅ ✅）。"""
        ref = self.attr_ref(node)
        if ref in self.declared_attrs:
            return
        self.declared_attrs.add(ref)
        self.emit("static PyObject *" + ref + " = NULL;")
        self.emit("if (" + ref + " == NULL) {")
        self.emit("    " + ref + " = PyUnicode_InternFromString(" + c_string(target_attr(node)) + ");")
        self.emit("    if (" + ref + " == NULL) goto cratix_error;")
        self.emit("}")

    def emit_try(self, node) -> None:
        """try/except ✅ —— 通过**每函数的 try 栈** + 错误处的 handler 分派 ✅ ✅。

        机制 ✅：
        - 每个 try 块压栈 ✅（记录它的编号 ✅ 与 handler 类型 ✅）；
        - 函数体出错 ✅ → 跳到 cratix_error ✅ → 取栈顶 ✅ → 按编号分派 ✅；
        - handler 体**内联** ✅（这样能看见外层局部变量 ✅ ✅）；
        - handler 跑完 ✅ → 跳到该 try 的结束标签 ✅ ✅（继续执行 ✅）；
        - 没有 handler 匹配 ✅ → 弹栈 ✅ → 继续外层 try ✅ ✅ → 都没有 ✅ → 真错误 ✅ ✅。
        """
        if node.get("orelse") or node.get("finalbody"):
            raise Unsupported("try/else/finally（尚未实现）")
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
        self.emit("    int cratix_ti = cratix_try_n++;")
        self.emit("    cratix_try_idx[cratix_ti] = " + str(try_id) + ";")
        for j, handler in enumerate(handlers):
            htype = handler.get("type")
            if htype is None:
                self.emit("    cratix_try_types[cratix_ti][" + str(j) + "] = PyExc_Exception;")
            else:
                hv = self.emit_expr(htype)
                self.emit("    cratix_try_types[cratix_ti][" + str(j) + "] = " + hv + ";")
        for stmt in node.get("body", []):
            self.emit_stmt(stmt)
        self.emit("    cratix_try_n--;")
        self.emit("    goto cratix_try_end_" + str(try_id) + ";")
        self.emit("}")
        # **结束标签** ✅ —— handler 跑完后跳到这里 ✅（继续执行 ✅ ✅）
        self.emit("cratix_try_end_" + str(try_id) + ": ;")

    def emit_try_handlers(self) -> list:
        """错误处的 handler 分派 ✅（switch ✅ —— handler 体需要看见外层局部变量 ✅ ✅）。

        **必须返回行列表** ✅ —— 直接 self.emit ✅ 会追加到 gen.lines ✅，
        而函数体早已 join ✅ ✅ → 分派代码会**丢失** ✅ ✅（实测 ✅）。
        """
        if not self.try_blocks:
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
            self.emit("                if (cratix_m >= 0) {")
            for j, handler in enumerate(handlers):
                name = handler.get("name")
                if name:
                    self.emit("                    if (cratix_m == " + str(j) + ") {")
                    self.emit("                        PyObject *cratix_hv = cratix_ev;")
                    self.emit("                        Py_INCREF(cratix_hv);")
                    self.emit("                        " + self.local_var(name) + " = cratix_hv;")
                    self.emit("                    }")
            for j, handler in enumerate(handlers):
                self.emit("                if (cratix_m == " + str(j) + ") {")
                for line in block["bodies"][j]:
                    self.emit(line)
                self.emit("                    goto cratix_try_end_" + str(try_id) + ";")
                self.emit("                }")
            self.emit("                }")
            self.emit("                break;")
            self.emit("            }")
        self.emit("        }")
        self.emit("        if (cratix_m < 0) {")
        self.emit("            cratix_try_n--;")
        self.emit("            PyErr_Restore(cratix_et, cratix_ev, cratix_etb);")
        self.emit("            goto cratix_error;")
        self.emit("        }")
        self.emit("        Py_XDECREF(cratix_et);")
        self.emit("        Py_XDECREF(cratix_etb);")
        self.emit("    }")
        return self.lines[start:]

    def emit_assign(self, target, value_var: str) -> None:
        """赋值到目标 ✅（value_var 的所有权转移给目标 ✅）。"""
        kind = target.kind
        if kind == "name":
            name = target.get("id")
            if name in self.scope.locals_:
                var = self.local_var(name)
                self.emit("Py_XDECREF(" + var + ");")
                self.emit(var + " = " + value_var + ";")
            else:
                self.emit("if (PyDict_SetItemString(cratix_globals, " + c_string(name) + ", " + value_var + ") < 0) goto cratix_error;")
                self.emit("Py_DECREF(" + value_var + ");")
            self.emit(value_var + " = NULL;")
            return
        if kind == "attr":
            obj = self.emit_expr(target.kids[0])
            self.emit_attr_name(target)
            self.emit("if (PyObject_SetAttr(" + obj + ", " + self.attr_ref(target) + ", " + value_var + ") < 0) goto cratix_error;")
            self.emit("Py_DECREF(" + value_var + ");")
            self.emit(value_var + " = NULL;")
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
                raise Unsupported("复合赋值到非局部目标")
            return
        if kind == "return":
            if node.kids:
                t = self.emit_expr(node.kids[0])
                self.emit("result = " + t + ";")
                self.emit(t + " = NULL;")
            else:
                self.emit("result = Py_NewRef(Py_None);")
            self.emit("goto cratix_done;")
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
            self.emit("while (1) {")
            test = self.emit_expr(node.kids[0])
            self.emit("    if (!cratix_truth(" + test + ")) break;")
            for stmt in node.get("body", []):
                self.emit_stmt(stmt)
            self.emit("}")
            for stmt in node.get("orelse", []):
                self.emit_stmt(stmt)
            return
        if kind == "for":
            it = self.new_helper()
            iter_expr = self.emit_expr(node.kids[1])
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
            self.emit("}")
            self.emit("Py_DECREF(" + it + ");")
            self.emit(it + " = NULL;")
            for stmt in node.get("orelse", []):
                self.emit_stmt(stmt)
            return
        if kind == "break":
            self.emit("break;")
            return
        if kind == "continue":
            self.emit("continue;")
            return
        if kind == "pass":
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
                self.emit(
                    "if (PyDict_SetItemString(cratix_globals, "
                    + c_string(bind_name)
                    + ", "
                    + bind_val
                    + ") < 0) goto cratix_error;"
                )
            return
        if kind == "try":
            self.emit_try(node)
            return
        if kind == "with":
            raise Unsupported("with（异常路径语义未实现）")
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
            self.emit(t + " = " + const_expr(node.get("value")) + ";")
            return t
        if kind == "name":
            name = node.get("id")
            if name in self.scope.locals_:
                var = self.local_var(name)
                self.emit(t + " = " + var + ";")
                self.emit("Py_XINCREF(" + t + ");")
            else:
                self.emit(t + " = cratix_load_name(cratix_globals, cratix_builtins, " + c_string(name) + ");")
                self.emit("if (" + t + " == NULL) goto cratix_error;")
            return t
        if kind == "attr":
            obj = self.emit_expr(node.kids[0])
            self.emit_attr_name(node)
            self.emit(t + " = PyObject_GetAttr(" + obj + ", " + self.attr_ref(node) + ");")
            self.emit("if (" + t + " == NULL) goto cratix_error;")
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
        obj = self.emit_expr(target.kids[0])
        # **vargs[0] 是对象** ✅（VectorcallMethod 的约定 ✅ ✅）
        self.emit("cratix_vargs[0] = " + obj + ";")
        self.emit("Py_INCREF(cratix_vargs[0]);")
        for i, child in enumerate(positional):
            v = self.emit_expr(child)
            self.emit("cratix_vargs[" + str(i + 1) + "] = " + v + ";")
            self.emit("Py_INCREF(cratix_vargs[" + str(i + 1) + "]);")
        if n_kw:
            self.emit("cratix_kwnames = PyTuple_New(" + str(n_kw) + ");")
            self.emit("if (cratix_kwnames == NULL) goto cratix_error;")
            for j, (name, child) in enumerate(keywords):
                v = self.emit_expr(child)
                self.emit("cratix_vargs[" + str(n_pos + 1 + j) + "] = " + v + ";")
                self.emit("Py_INCREF(cratix_vargs[" + str(n_pos + 1 + j) + "]);")
                self.emit("PyObject *cratix_kn = PyUnicode_FromString(" + c_string(name) + ");")
                self.emit("if (cratix_kn == NULL) goto cratix_error;")
                self.emit("PyTuple_SET_ITEM(cratix_kwnames, " + str(j) + ", cratix_kn);")
        else:
            self.emit("cratix_kwnames = NULL;")
        self.emit("cratix_mname = PyUnicode_FromString(" + c_string(target.get("attr")) + ");")
        self.emit("if (cratix_mname == NULL) goto cratix_error;")
        self.emit(t + " = PyObject_VectorcallMethod(cratix_mname, cratix_vargs, " + str(n + 1) + " | PY_VECTORCALL_ARGUMENTS_OFFSET, cratix_kwnames);")
        self.emit("Py_DECREF(cratix_mname);")
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
        for i, child in enumerate(positional):
            v = self.emit_expr(child)
            self.emit("cratix_vargs[" + str(i) + "] = " + v + ";")
            self.emit("Py_INCREF(cratix_vargs[" + str(i) + "]);")
        if n_kw:
            self.emit("cratix_kwnames = PyTuple_New(" + str(n_kw) + ");")
            self.emit("if (cratix_kwnames == NULL) goto cratix_error;")
            for j, (name, child) in enumerate(keywords):
                v = self.emit_expr(child)
                self.emit("cratix_vargs[" + str(n_pos + j) + "] = " + v + ";")
                self.emit("Py_INCREF(cratix_vargs[" + str(n_pos + j) + "]);")
                self.emit("PyObject *cratix_kn = PyUnicode_FromString(" + c_string(name) + ");")
                self.emit("if (cratix_kn == NULL) goto cratix_error;")
                self.emit("PyTuple_SET_ITEM(cratix_kwnames, " + str(j) + ", cratix_kn);")
        else:
            self.emit("cratix_kwnames = NULL;")
        self.emit(t + " = PyObject_Vectorcall(" + func + ", cratix_vargs, " + str(n) + ", cratix_kwnames);")
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

    def emit_joinedstr(self, node, t) -> None:
        parts = self.new_tmp()
        self.emit(parts + " = PyList_New(0);")
        self.emit("if (" + parts + " == NULL) goto cratix_error;")
        for child in node.kids:
            if child.kind == "const":
                piece = self.new_tmp()
                self.emit(piece + " = PyUnicode_FromString(" + c_string(child.get("value")) + ");")
                self.emit("if (" + piece + " == NULL) goto cratix_error;")
            else:
                piece = self.emit_expr(child)
            self.emit("if (PyList_Append(" + parts + ", " + piece + ") < 0) goto cratix_error;")
        sep = self.new_tmp()
        self.emit(sep + " = PyUnicode_FromString(\"\");")
        self.emit("if (" + sep + " == NULL) goto cratix_error;")
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
            self.emit(t + " = cratix_format(" + base + ", NULL);")
        else:
            spec_str = self.emit_expr(spec)
            self.emit(t + " = cratix_format(" + base + ", " + spec_str + ");")
        self.emit("if (" + t + " == NULL) goto cratix_error;")

    def emit_comprehension(self, node, t, kind) -> None:
        """推导式 ✅（v1 只支持**单个**生成器 ✅ → 多生成器整函数回退 ✅）。"""
        kids = list(node.kids)
        offset = 2 if kind == "dictcomp" else 1
        gens = kids[offset:]
        if len(gens) != 1:
            raise Unsupported("多生成器推导式")
        target, iterable, ifs = gens[0]
        if target.kind != "name":
            raise Unsupported("推导式目标解包")
        it = self.new_helper()
        src = self.emit_expr(iterable)
        self.emit(it + " = PyObject_GetIter(" + src + ");")
        self.emit("if (" + it + " == NULL) goto cratix_error;")
        if kind == "dictcomp":
            self.emit(t + " = PyDict_New();")
        elif kind == "setcomp":
            self.emit(t + " = PySet_New(NULL);")
        else:
            self.emit(t + " = PyList_New(0);")
        self.emit("if (" + t + " == NULL) goto cratix_error;")
        self.emit("while (1) {")
        item = self.new_tmp()
        self.emit("    " + item + " = PyIter_Next(" + it + ");")
        self.emit("    if (" + item + " == NULL) { if (PyErr_Occurred()) goto cratix_error; break; }")
        # 推导式有**独立作用域** ✅ → 目标绑到临时变量 ✅（不污染函数局部 ✅）
        bind = self.new_tmp()
        self.emit("    " + bind + " = " + item + ";")
        self.emit("    Py_INCREF(" + bind + ");")
        self.emit("    " + item + " = NULL;")
        for cond in ifs:
            c = self.emit_expr(cond)
            self.emit("    if (!cratix_truth(" + c + ")) continue;")
        if kind == "dictcomp":
            k = self.emit_expr(kids[0])
            v = self.emit_expr(kids[1])
            self.emit("    if (PyDict_SetItem(" + t + ", " + k + ", " + v + ") < 0) goto cratix_error;")
        else:
            elt = self.emit_expr(kids[0])
            if kind == "setcomp":
                self.emit("    if (PySet_Add(" + t + ", " + elt + ") < 0) goto cratix_error;")
            else:
                self.emit("    if (PyList_Append(" + t + ", " + elt + ") < 0) goto cratix_error;")
        self.emit("}")
        self.emit("Py_DECREF(" + it + ");")
        self.emit(it + " = NULL;")

def target_attr(node) -> str:
    return node.get("attr")


def c_bytes_literal(data: bytes) -> str:
    """bytes 字面量 ✅（逐字节转义 ✅ —— 避免十六进制转义后面跟十六进制字符的歧义 ✅）。"""
    out = ["\""]
    for byte in data:
        out.append("\\x%02x" % byte)
    out.append("\"")
    return "".join(out)