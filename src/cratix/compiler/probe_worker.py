"""探针子进程 ✅ —— 观测一个模块的可观测行为 ✅ 并输出 JSON ✅。

用法：``python -m cratix.compiler.probe_worker <spec.json>`` ✅
spec: {"mode": "source"|"aot", "path": ..., "module": ..., "init": ...} ✅

**为什么必须用子进程** ✅：同一个解释器里先后加载两个版本 ✅ 会污染 `sys.modules` ✅
（cypack 曾因此产生 58 → 22 → 0 次误失败 ✅）→ 两个独立进程 ✅ 各自干净 ✅。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_source(path: str, name: str):
    """按文件路径加载源码模块 ✅（编译成 .pyc 后再加载 ✅ —— 与产物形态一致 ✅）。"""
    source = Path(path)
    cache = Path(source).with_suffix(".probe.pyc")
    import py_compile

    try:
        py_compile.compile(str(source), cfile=str(cache), doraise=True)
        loader = importlib.machinery.SourcelessFileLoader(name, str(cache))
        spec = importlib.util.spec_from_file_location(name, str(cache), loader=loader)
    except Exception:
        loader = importlib.machinery.SourceFileLoader(name, str(source))
        spec = importlib.util.spec_from_file_location(name, str(source), loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_aot(pyd: str, init_symbol: str):
    """用 ctypes 调 init 符号加载编译产物 ✅（与 stub 的加载路径一致 ✅）。"""
    from . import loader

    return loader.load_compiled_module(Path(pyd), init_symbol)


def observe(module) -> dict:
    """采集模块的可观测行为 ✅（只采**稳定**的语义 ✅ —— 不比实现细节 ✅）。"""
    names = sorted(n for n in vars(module) if not n.startswith("__"))
    functions = {}
    constants = {}
    for name in names:
        value = getattr(module, name)
        if callable(value):
            functions[name] = _describe(value)
        else:
            try:
                constants[name] = repr(value)
            except Exception:  # noqa: BLE001
                constants[name] = "<不可 repr>"
    return {"names": names, "functions": functions, "constants": constants}


def _describe(fn) -> dict:
    """函数的**元数据** ✅ —— 与实现无关 ✅（不采字节码 ✅）。"""
    out = {
        "name": getattr(fn, "__name__", None),
        "qualname": getattr(fn, "__qualname__", None),
        "doc": (getattr(fn, "__doc__", None) or "")[:200],
    }
    try:
        import inspect

        sig = inspect.signature(fn)
        out["params"] = [
            {
                "name": p.name,
                "kind": str(p.kind),
                "has_default": p.default is not inspect.Parameter.empty,
                "default": repr(p.default) if p.default is not inspect.Parameter.empty else None,
            }
            for p in sig.parameters.values()
        ]
    except Exception:  # noqa: BLE001 - C 函数没有签名 ✅
        out["params"] = None
    return out

def main() -> int:
    """子进程入口 ✅ —— 读 spec ✅ 观测 ✅ 输出 JSON ✅。"""
    import json
    import os

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    try:
        spec = json.loads(sys.argv[1])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "spec 解析失败：" + str(exc)}))
        return 1
    try:
        if spec.get("mode") == "aot":
            module = _load_aot(spec["path"], spec["init"])
        else:
            module = _load_source(spec["path"], spec["module"])
        data = observe(module)
        data["ok"] = True
    except BaseException as exc:  # noqa: BLE001 - 观测失败要如实上报 ✅
        data = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())