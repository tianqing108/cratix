"""加载编译产物的辅助函数 ✅（与 stub 的加载路径等价 ✅）。

**为什么用 ctypes 而不是标准扩展加载器** ✅：
标准加载器要求单阶段模块带 `m_def` ✅ —— 而 `PyModule_SetModuleDef()` 在 3.14 **没有导出** ✅
（实测 ✅）→ 走标准加载器会报 "did not return a valid extension module" ✅。
我们生成的模块是 `PyModule_New` + `PyModule_AddFunctions` ✅（没有 m_def ✅）——
这与 stub 的加载方式一致 ✅（stub 直接调用 init 符号 ✅ 并使用返回值 ✅）。
"""

from __future__ import annotations

import ctypes
from pathlib import Path


def load_compiled_module(pyd: Path, init_symbol: str):
    """加载编译产物并返回模块对象 ✅（新引用 ✅ —— 调用方负责释放 ✅）。"""
    pyd = Path(pyd).resolve()
    # **PyDLL 而不是 CDLL** ✅ —— CDLL 会在调用期间**释放 GIL** ✅
    # → 而 Python C-API 需要持有 GIL ✅ → 崩溃（访问违例 ✅）。
    dll = ctypes.PyDLL(str(pyd))
    init = getattr(dll, init_symbol)
    init.restype = ctypes.py_object
    init.argtypes = []
    module = init()
    if module is None:
        raise RuntimeError("init 符号返回 NULL：" + init_symbol)
    return module