"""差分比对：源码版 vs IR-VM 版 ✅。

这是 AOT 编译器的**语义安全网** ✅ —— 编译产物必须与源码行为一致 ✅。
先用 IR-VM（黄金模型 ✅）比对 ✅：不依赖 MSVC ✅ → 快 ✅ → 能定位前端语义 bug ✅。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

from .. import builder
from . import irvm
from ..frontend.scope import analyze_function, module_bindings
from ..frontend.subset import Unsupported, check_function


def load_module(path: Path, name: str | None = None):
    """按文件路径加载模块 ✅（不依赖 sys.path ✅）。"""
    path = Path(path).resolve()
    name = name or ("cratix_corpus_" + path.stem)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载：" + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def module_tree(path: Path) -> ast.Module:
    return ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))


def find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    raise KeyError("模块里没有函数：" + name)


def classify(tree: ast.Module, name: str) -> tuple[bool, str]:
    """判定函数能否 AOT ✅ —— 返回 (supported, reason ✅)。"""
    try:
        node = find_function(tree, name)
        # **必须传模块绑定** ✅ —— 否则模块级的类/常量会被当成闭包 ✅ ✅
        check_function(node, analyze_function(node, module_bindings(tree)))
        return True, ""
    except Unsupported as exc:
        return False, exc.reason


def ir_function(tree: ast.Module, name: str, globals_dict: dict):
    """把源码函数编译成 IR 函数对象 ✅（黄金模型 ✅）。"""
    node = find_function(tree, name)
    built = builder.build_stmt(node)
    return irvm.Function(built.get("name"), built.get("params"), built.get("body", []), globals_dict)


def _capture(fn, args, kwargs):
    """捕获一次调用的结果 ✅（异常只比**类型** ✅ —— 消息是实现细节 ✅）。"""
    try:
        value = fn(*args, **(kwargs or {}))
        return {"status": "ok", "value": repr(value), "type": type(value).__name__}
    except BaseException as exc:  # noqa: BLE001 - 异常也要比对 ✅
        return {
            "status": "exc",
            "type": type(exc).__name__,
            "value": str(exc),
        }


def _same(a: dict, b: dict) -> bool:
    """结果可比性 ✅：
    - 正常返回：类型 + repr 都一致 ✅；
    - 抛异常：**异常类型**一致即可 ✅（消息是实现细节 ✅ —— AOT 产物也不会逐字相同 ✅）。
    """
    if a["status"] != b["status"]:
        return False
    if a["status"] == "exc":
        return a["type"] == b["type"]
    return a["type"] == b["type"] and a["value"] == b["value"]


def compare_function(path: Path, name: str, args=(), kwargs=None) -> dict:
    """比对一个函数在源码版与 IR-VM 版的行为 ✅。"""
    module = load_module(path)
    tree = module_tree(path)
    globals_dict = dict(vars(module))
    source_fn = getattr(module, name)
    ir_fn = ir_function(tree, name, globals_dict)
    source_result = _capture(source_fn, args, kwargs)
    ir_result = _capture(ir_fn, args, kwargs)
    match = _same(source_result, ir_result)
    return {
        "name": name,
        "match": match,
        "source": source_result,
        "irvm": ir_result,
    }