"""AOT 编译器驱动：源码模块 -> IR -> C -> .pyd ✅。

v1 的判定与生成都做**函数级** ✅：
    - 能 AOT 的函数 -> 生成原生函数 ✅；
    - 不能 AOT 的函数 -> 记下原因 ✅（后续做内嵌 code object 的函数级回退 ✅）。

模块级只支持**常量赋值**与 **import** ✅（其余 -> 整个模块回退 ✅）。
"""

from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import builder
from .backend import codegen
from .backend.runtime import HEADER_NAME, RUNTIME_H
from .frontend.scope import Scope, analyze_function
from .frontend.subset import Unsupported, check_function


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
    module_ok: bool = True
    module_reason: str = ""
    key_names: list = field(default_factory=list)
    module_gen: object = None

    @property
    def supported_names(self) -> list:
        return [p.name for p in self.plans if p.supported]

    @property
    def fallback_names(self) -> list:
        return [p.name for p in self.plans if not p.supported]


def init_symbol_for(module_name: str) -> str:
    """唯一的 init 符号 ✅（PyInit_<末段> 会被同名模块冲突 ✅ —— 用哈希 ✅）。"""
    digest = hashlib.sha1(module_name.encode("utf-8")).hexdigest()[:10]
    return "PyInit_cratix_" + digest


def _module_level_supported(stmt) -> bool:
    if isinstance(stmt, ast.Assign):
        return all(isinstance(t, ast.Name) for t in stmt.targets)
    if isinstance(stmt, ast.Expr):
        # 只允许文档字符串 ✅（其它表达式可能有副作用 ✅ -> 整个模块回退 ✅）
        return isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
    if isinstance(stmt, ast.Import):
        return True
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True
    return False


def compile_module(path: Path, module_name: str | None = None) -> ModuleCompile:
    """把一个 Python 模块编译成 C ✅（函数级判定 ✅）。"""
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module_name = module_name or path.stem
    result = ModuleCompile(module_name=module_name, init_symbol=init_symbol_for(module_name))

    # --- 模块级语句 ✅ ---
    module_stmts = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not _module_level_supported(stmt):
            result.module_ok = False
            result.module_reason = "模块级语句不支持：" + type(stmt).__name__
            return result
        if isinstance(stmt, ast.Expr):
            continue  # 文档字符串 ✅ —— 无需编译 ✅
        module_stmts.append(stmt)

    # --- 函数级判定 + 生成 ✅ ---
    key_names = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        scope = analyze_function(stmt)
        try:
            check_function(stmt, scope)
            built = builder.build_stmt(stmt)
            gen = codegen.FunctionGen(stmt.name, built.get("params"), built.get("body", []), scope)
            gen.emit_binding()
            for body_stmt in built.get("body", []):
                gen.emit_stmt(body_stmt)
        except Unsupported as exc:
            result.plans.append(FunctionPlan(stmt.name, False, exc.reason))
            continue
        result.functions.append(gen)
        result.plans.append(FunctionPlan(stmt.name, True, ""))
        for name in list(gen.params.get("names", [])) + list(gen.params.get("kwonly", [])):
            key_names.append(name)

    # --- 模块级 body ✅ ---
    module_gen = None
    if module_stmts:
        try:
            module_ir = builder.build_module(ast.Module(body=module_stmts, type_ignores=[]))
            module_gen = codegen.FunctionGen("<module>", None, [], Scope(name="<module>"))
            for stmt in module_ir.get("body", []):
                module_gen.emit_stmt(stmt)
        except Unsupported as exc:
            result.module_ok = False
            result.module_reason = exc.reason
            return result

    result.key_names = key_names
    result.module_gen = module_gen
    result.c_source = codegen.generate_module(
        module_name,
        result.init_symbol,
        result.functions,
        key_names,
        module_gen,
    )
    return result

def build_pyd(module_compile: ModuleCompile, out_dir: Path, python_prefix: Path | None = None,
              alias_init: str | None = None) -> Path:
    """把生成的 C 编译成 .pyd ✅（MSVC ✅ —— 工具链定位复用 stubbuild ✅）。

    产物文件名用 **init 符号的末段** ✅（cratix_<hash>.pyd ✅）——
    这样标准的 ExtensionFileLoader 也能按名字找到 PyInit ✅（开发期测试方便 ✅）；
    打包后由 cratix_loader 按 meta.json 里的 init 显式调用 ✅（不依赖文件名 ✅）。
    """
    from ..packaging import stubbuild

    python_prefix = Path(python_prefix or sys.base_prefix)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = module_compile.init_symbol[len("PyInit_"):]
    c_path = out_dir / (stem + ".c")
    if alias_init:
        module_compile.c_source = codegen.generate_module(
            module_compile.module_name,
            module_compile.init_symbol,
            module_compile.functions,
            module_compile.key_names,
            module_compile.module_gen,
            alias_init,
        )
    c_path.write_text(module_compile.c_source, encoding="utf-8", newline="")
    (out_dir / HEADER_NAME).write_text(RUNTIME_H, encoding="utf-8", newline="")

    vcvars = stubbuild.find_vcvars()
    include = python_prefix / "include"
    libs = python_prefix / "libs"
    lib_name = "python%d%d.lib" % (sys.version_info.major, sys.version_info.minor)
    cmd = (
        'call "' + str(vcvars) + '" >nul && cd /d "' + str(out_dir) + '" && '
        # **必须用 /MD** ✅（DLL CRT ✅）—— 扩展模块要与解释器用**同一个 CRT** ✅；
        # 用 /MT（静态 CRT ✅）会让解释器用 DLL CRT 释放我们的内存 ✅ → 堆损坏 ✅。
        'cl /nologo /O2 /MD /W3 /utf-8 /c /Brepro "' + c_path.name + '" /I"' + str(include) + '" '
        '&& link /DLL /Brepro /OUT:"' + stem + '.pyd" "' + stem + '.obj" '
        '/LIBPATH:"' + str(libs) + '" ' + lib_name
    )
    stubbuild.run_cmd(cmd)
    pyd = out_dir / (stem + ".pyd")
    if not pyd.is_file():
        raise RuntimeError("未生成 .pyd：" + str(pyd))
    return pyd