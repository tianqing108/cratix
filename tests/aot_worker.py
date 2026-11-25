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


def op_try(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_try.py", "cratix_corpus_try", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "div": mod.safe_div(6, 3),
        "div_zero": mod.safe_div(1, 0),
        "div2_zero": mod.safe_div2(1, 0),
        "div2_type": mod.safe_div2("a", 1),
        "nested": mod.nested(0),
    }


def op_try(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_try.py", "cratix_corpus_try", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "div": mod.safe_div(6, 3),
        "div_zero": mod.safe_div(1, 0),
        "div2_zero": mod.safe_div2(1, 0),
        "div2_type": mod.safe_div2("a", 1),
        "nested": mod.nested(0),
    }


def op_with(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_with.py", "cratix_corpus_with", out_dir)

    def run(name):
        mod.LOG.clear()
        return getattr(mod, name)()

    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "simple": run("simple"),
        "nested": run("nested_exit_order"),
        "suppress": run("suppress_exc"),
        "propagate": run("propagate_exc"),
        "return_inside": run("return_inside"),
        "break_inside": run("break_inside_with"),
        "continue_inside": run("continue_inside_with"),
        "with_in_try": run("with_in_try"),
        "try_in_with": run("try_in_with"),
        "loop_else_break": run("loop_else_break"),
        "loop_else_no_break": run("loop_else_no_break"),
        "while_else_break": run("while_else_break"),
        "while_else_no_break": run("while_else_no_break"),
        "pure_suppress": mod.pure_with_suppress(),
        "pure_propagate": mod.pure_with_propagate(),
        "pure_with": mod.pure_with(True),
        "pure_nested": mod.pure_nested(),
    }


def op_comprehensions(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_comprehensions.py", "cratix_corpus_comp", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "single": repr(mod.single([3, -1, 2])),
        "multi": repr(mod.multi([1, 2, 3])),
        "nested": repr(mod.nested([[1, 2], [3]])),
        "unpack": repr(mod.unpack([(1, 2), (3, 4)])),
        "dict_multi": repr(mod.dict_multi({"a": [1, 2]})),
        "scoped": repr(mod.scoped([1, 2])),
        "self_iter": repr(mod.self_iter()),
        "setcomp_cond": repr(mod.setcomp_cond([1, 2, 3, 4, 5])),
        "filtered_multi": repr(mod.filtered_multi([1, 2, 3])),
        "shadowed": repr(mod.shadowed([1, 2])),
        "str_join": repr(mod.str_join(["a", "bb", "ccc"])),
    }


def op_decorators(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_decorators.py", "cratix_corpus_dec", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "module_level": repr(mod.module_level()),
        "box_ops": repr(mod.box_ops()),
        "marked_ops": repr(mod.marked_ops()),
    }


def op_extras(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_extras.py", "cratix_corpus_extras", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "module_ok": mc.module_ok,
        "counter": mod.counter(),
        "accumulate": mod.accumulate(),
        "local_import": mod.local_import(),
        "imported": mod.imported(),
        "reset": mod.reset_counter(),
        "after_reset": mod.read_counter(),
    }


def op_finally(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_finally.py", "cratix_corpus_finally", out_dir)
    cases = [
        "simple", "with_exception", "with_return", "with_break", "with_continue",
        "else_runs", "else_skipped", "nested", "value_after",
    ]
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "values": {name: getattr(mod, name)() for name in cases},
    }


def op_methods(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_methods.py", "cratix_corpus_methods", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "self_calls": mod.self_calls(10),
        "shadow_calls": mod.shadow_calls(10),
        "attribute_reads": mod.attribute_reads(10),
        "direct_used": "cratix_fn_v_Machine_step(" in mc.c_source,
        "shadow_direct": "cratix_fn_v_Shadowed_step(" in mc.c_source,
    }


def op_genexp(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_genexp.py", "cratix_corpus_genexp", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "helpers": sorted(mc.genexp_sources),
        "total": mod.total([1, 2, 3]),
        "filtered": mod.filtered([1, 2, 3, 4]),
        "nested": mod.nested([[1, 2], [3]]),
        "lazy": list(mod.lazy(range(10000000))),
        "passthrough": list(mod.passthrough()),
        "shadowed": list(mod.shadowed([1, 22])),
        "captured": mod.captured([1, 2], 10),
        "holder": mod.Holder([1, 2]).result(),
    }


def op_metadata(out_dir: Path) -> dict:
    """**元数据保真** ✅ —— 源码与 AOT 逐字段比对 ✅。"""
    import importlib.util
    import inspect

    name = "cratix_meta_cmp"
    corpus = CORPUS / "corpus_metadata.py"
    spec = importlib.util.spec_from_file_location(name, corpus)
    src_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(src_mod)

    mc = driver.compile_module(corpus, name)
    pyd = driver.build_pyd(mc, out_dir)
    aot_mod = loader.load_compiled_module(pyd, mc.init_symbol)

    def signature(fn):
        try:
            return str(inspect.signature(fn))
        except Exception as exc:  # noqa: BLE001
            return "<" + type(exc).__name__ + ">"

    def describe(mod):
        out = {}
        for fn_name in ("documented", "undocumented", "defaults_only"):
            fn = getattr(mod, fn_name)
            out[fn_name] = {
                "doc": fn.__doc__,
                "name": fn.__name__,
                "qualname": getattr(fn, "__qualname__", None),
                "module": getattr(fn, "__module__", None),
                "signature": signature(fn),
            }
        cls = mod.Widget
        out["class"] = {
            "doc": cls.__doc__,
            "qualname": getattr(cls, "__qualname__", None),
            "module": getattr(cls, "__module__", None),
            "kind": cls.kind,
        }
        inst = cls(3)
        for method_name in ("__init__", "area", "plain"):
            bound = getattr(inst, method_name)
            out["method:" + method_name] = {
                "doc": bound.__doc__,
                "qualname": getattr(bound, "__qualname__", None),
                "name": getattr(bound, "__name__", None),
                "signature": signature(bound),
            }
        out["values"] = [inst.area(2), inst.area(), inst.plain()]
        return out

    src_snapshot = describe(src_mod)
    aot_snapshot = describe(aot_mod)
    mismatches = []
    for key in sorted(src_snapshot):
        if src_snapshot[key] != aot_snapshot.get(key):
            mismatches.append({"key": key, "src": src_snapshot[key], "aot": aot_snapshot.get(key)})
    return {"mismatches": mismatches, "fields": len(src_snapshot)}


def op_leaks(out_dir: Path) -> dict:
    """**引用释放** ✅ —— 用 getrefcount 直接量泄漏 ✅（确定性 ✅，不靠内存采样 ✅）。"""
    mc, mod = _compile("corpus_leaks.py", "cratix_corpus_leaks", out_dir)
    results = {"supported": sorted(mc.supported_names)}

    class Sentry:
        pass

    cases = [
        ("loop_refs", 200),
        ("comp_refs", 50),
        ("with_continue", 100),
        ("with_break", 10),
        ("try_refs", 100),
        ("call_refs", 100),
        ("call_fn_refs", 100),
    ]
    for name, n in cases:
        sentry = Sentry()
        before = sys.getrefcount(sentry)
        value = getattr(mod, name)(sentry, n)
        after = sys.getrefcount(sentry)
        results[name + "_delta"] = after - before
        results[name + "_value"] = value
    return results


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
        elif op == "try":
            data = op_try(out_dir)
        elif op == "with":
            data = op_with(out_dir)
        elif op == "decorators":
            data = op_decorators(out_dir)
        elif op == "comprehensions":
            data = op_comprehensions(out_dir)
        elif op == "leaks":
            data = op_leaks(out_dir)
        elif op == "metadata":
            data = op_metadata(out_dir)
        elif op == "genexp":
            data = op_genexp(out_dir)
        elif op == "methods":
            data = op_methods(out_dir)
        elif op == "finally":
            data = op_finally(out_dir)
        elif op == "extras":
            data = op_extras(out_dir)
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