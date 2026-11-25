"""AOT 测试的**子进程** ✅。

**为什么必须独立进程** ✅：在同一进程里连续加载**多个** AOT 模块 ✅ 会破坏解释器状态 ✅
（实测：3.14 的自适应字节码缓存 ✅ —— "Executing a cache" ✅）。
产品路径不受影响 ✅ —— 一个应用只加载自己的模块 ✅ ✅。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler import driver, loader

CORPUS = ROOT / "tests" / "corpus"


def _compile(name: str, module_name: str, out_dir: Path):
    """编译语料模块 ✅ 并加载 ✅（**在本进程内** ✅）。"""
    mc = driver.compile_module(CORPUS / name, module_name)
    if not mc.module_ok:
        raise SystemExit("module_ok 失败：" + mc.module_reason)
    pyd = driver.build_pyd(mc, out_dir)
    return mc, loader.load_compiled_module(pyd, mc.init_symbol)


def op_basic(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_basic.py", "cratix_corpus_basic", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "add": mod.add(3, 4),
        "control": mod.control(7),
        "strings": mod.strings("bob", 3),
        "containers": repr(mod.containers([1, 2, 3])),
    }


def op_classes(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_classes.py", "cratix_corpus_cls", out_dir)
    point = mod.Point(3, 4)
    return {
        "classes": [c["name"] for c in mc.classes],
        "kind": mod.Point.kind,
        "norm2": point.norm2(),
        "describe": point.describe(),
        "shifted": point.shifted(1).describe(),
        "shifted2": point.shifted(1, 2).describe(),
        "x": point.x,
        "y": point.y,
        "is_instance": isinstance(point, mod.Point),
        "type_name": mod.Point.__name__,
    }


def op_fallback(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_unsupported.py", "cratix_corpus_unsup", out_dir)
    return {
        "fallback": sorted(mc.fallback_names),
        "embedded": sorted(mc.fallback_code),
        "gen": list(mod.gen(3)),
        "uses_global": mod.uses_global(),
        "closure": mod.closure(5)(2),
    }


def main() -> int:
    """子进程入口 ✅ —— 输出 JSON ✅。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    op = sys.argv[1]
    out_dir = Path(sys.argv[2]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if op == "basic":
            data = op_basic(out_dir)
        elif op == "classes":
            data = op_classes(out_dir)
        elif op == "fallback":
            data = op_fallback(out_dir)
        else:
            raise SystemExit("未知操作：" + op)
    except BaseException as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}, ensure_ascii=False))
        return 1
    data["ok"] = True
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())