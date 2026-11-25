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


def op_statements(out_dir: Path) -> dict:
    """**语句组合 + 类特性差分** ✅。"""
    import importlib.util

    mc, mod = _compile("corpus_statements.py", "cratix_corpus_statements", out_dir)
    corpus = Path(__file__).resolve().parent / "corpus" / "corpus_statements.py"
    spec = importlib.util.spec_from_file_location("cratix_src_statements", corpus)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)

    cases = [
        ("for_else", ([1, 2],)),
        ("for_else", ([1, 9],)),
        ("while_else", ([1, 2],)),
        ("while_else", ([1, -1],)),
        ("nested_try", ([1],)),
        ("nested_try", ([2],)),
        ("try_in_loop", ([1, -1, 2],)),
        ("finally_overrides", ([],)),
        ("multiple_with", ([], [])),
        ("chained_calls", ("Hello World Again",)),
        ("assert_ok", ([1, 2, 3],)),
        ("global_write", (5,)),
        ("global_write", (2,)),
        ("class_features", ()),
        ("dict_of_lists", (["a", "b", "a"], [1, 2, 3])),
        ("nested_mutation", ()),
        ("truthiness", ([0, 1, "", "x", [], [1]],)),
    ]

    mismatches = []
    for name, args in cases:
        try:
            expected = repr(getattr(source, name)(*args))
        except Exception as exc:  # noqa: BLE001
            expected = "EXC:" + type(exc).__name__ + ":" + str(exc)
        try:
            actual = repr(getattr(mod, name)(*args))
        except Exception as exc:  # noqa: BLE001
            actual = "EXC:" + type(exc).__name__ + ":" + str(exc)
        if actual != expected:
            mismatches.append(f"{name}{args}: aot={actual} src={expected}")

    return {
        "module_ok": mc.module_ok,
        "fallback": sorted(mc.fallback_names),
        "compared": len(cases),
        "mismatches": mismatches,
    }


def op_namespace(out_dir: Path) -> dict:
    """**命名空间干净** ✅ —— AOT 模块不该泄漏编译器内部名字 ✅（#129 ✅）。"""
    leaked = {}
    for corpus in ("corpus_closures.py", "corpus_genexp.py", "corpus_lambda.py",
                   "corpus_local_class.py", "corpus_classes.py"):
        path = Path(__file__).resolve().parent / "corpus" / corpus
        if not path.is_file():
            continue
        mc, mod = _compile(corpus, "cratix_ns_" + path.stem, out_dir)
        # **装饰器跳板**（`_cratix_tb_*` / `_cratix_raw_*`）暂时留在 globals ✅ ——
        # 它们是 **Python 函数** ✅，按名字查 `__globals__` ✅（下一轮改成"把函数当参数传"就能搬走 ✅）。
        # **不再有任何豁免** ✅ —— 装饰器跳板也改成"把函数当参数传"了 ✅（#129 收尾 ✅）
        names = [n for n in mod.__dict__ if n.startswith("cratix_") or n.startswith("_cratix_")]
        if names:
            leaked[corpus] = names[:8]
    return {"leaked": leaked}


def op_forms(out_dir: Path) -> dict:
    """**常见形态差分** ✅ —— 源码版当基准 ✅ 逐项比 ✅。"""
    import importlib.util

    mc, mod = _compile("corpus_forms.py", "cratix_corpus_forms", out_dir)
    corpus = Path(__file__).resolve().parent / "corpus" / "corpus_forms.py"
    spec = importlib.util.spec_from_file_location("cratix_src_forms", corpus)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)

    cases = [
        ("builtins", ([3, 1, 2],)),
        ("string_methods", ("hello world",)),
        ("list_methods", ([3, 1, 2],)),
        ("dict_methods", ([("a", 1), ("b", 2)],)),
        ("set_methods", ([1, 2, 3],)),
        ("slicing", ([1, 2, 3, 4, 5], "hello")),
        ("unpacking", ([1, 2, 3, 4],)),
        ("comprehensions", ([1, 2, 3, 4],)),
        ("fstrings", ("x", 7, 3.14159)),
        ("exception_flow", ([1, 2, 3],)),
        ("exception_flow", ([1, -2, 3],)),
        ("try_finally", ([],)),
        ("loops", ([1, 2, 0, 20, 5],)),
        ("with_class", ([1, 2, 3],)),
        ("use_with", ([],)),
    ]

    mismatches = []
    for name, args in cases:
        try:
            expected = getattr(source, name)(*args)
            expected_repr = repr(expected)
        except Exception as exc:  # noqa: BLE001
            expected_repr = "EXC:" + type(exc).__name__ + ":" + str(exc)
        try:
            actual = getattr(mod, name)(*args)
            actual_repr = repr(actual)
        except Exception as exc:  # noqa: BLE001
            actual_repr = "EXC:" + type(exc).__name__ + ":" + str(exc)
        if actual_repr != expected_repr:
            mismatches.append(f"{name}{args}: aot={actual_repr} src={expected_repr}")

    return {
        "module_ok": mc.module_ok,
        "fallback": sorted(mc.fallback_names),
        "compared": len(cases),
        "mismatches": mismatches,
    }


def op_operators(out_dir: Path) -> dict:
    """**运算符差分** ✅ —— 同一个语料分别用**源码**和 **AOT** 跑 ✅，逐个比 ✅。"""
    import importlib.util

    mc, mod = _compile("corpus_operators.py", "cratix_corpus_operators", out_dir)
    corpus = Path(__file__).resolve().parent / "corpus" / "corpus_operators.py"
    spec = importlib.util.spec_from_file_location("cratix_src_operators", corpus)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)

    # **参数要挑"所有被比函数都接受"的** ✅（`"ab" - "cd"` 会抛 ✅ -> 那就不是同一个结果可比了 ✅）
    binaries = [(2, 3), (7, 2), (-5, 2), (0, 1), (9, 4)]
    strings = [("ab", "cd")]
    compares = [(1, 2), (2, 2), (3, 2), ("a", "b"), (0, 0)]
    members = [("work", ("work", "life")), ("zzz", ("work", "life")),
               (2, [1, 2, 3]), (9, [1, 2, 3]), ("ell", "hello"), ("zzz", "hello")]
    pairs = {
        "add": binaries + strings, "sub": binaries, "mul": binaries,
        "bitand": [(6, 3), (5, 1)], "bitor": [(6, 3), (5, 1)], "bitxor": [(6, 3), (5, 1)],
        "logical_and": [(1, 0), (0, 0), (2, 3)], "logical_or": [(0, 0), (0, 5), (1, 2)],
        "truediv": [(7, 2), (9, 3)], "floordiv": [(7, 2), (-5, 2)], "mod": [(7, 2), (-5, 2)],
        "power": [(2, 10), (3, 3)], "lshift": [(1, 4)], "rshift": [(16, 2)],
        "identity": [(1, 1), (1, 2), ("a", "a")], "not_identity": [(1, 1), (1, 2)],
        "contains": members, "not_contains": members,
        "lt": compares, "le": compares, "eq": compares, "ne": compares,
        "ge": compares, "gt": compares,
    }
    unaries = [("invert", (5,)), ("negate", (7,)), ("positive", (-3,)),
               ("logical_not", (0,)), ("logical_not", (5,))]
    triples = [("chained_range", (1, 2, 3)), ("chained_range", (1, 5, 3)),
               ("chained_mixed", (1, 2, 3)), ("chained_contains", (1, 2, (2, 3))),
               ("ternary", (True, "a", "b")), ("ternary", (False, "a", "b")),
               ("walrus_in_condition", ([1, 2, 3],))]

    mismatches = []
    compared = 0
    for name, args_list in pairs.items():
        for args in args_list:
            expected = getattr(source, name)(*args)
            actual = getattr(mod, name)(*args)
            compared += 1
            if actual != expected:
                mismatches.append(f"{name}{args}: aot={actual!r} src={expected!r}")
    for name, args in unaries + triples:
        expected = getattr(source, name)(*args)
        actual = getattr(mod, name)(*args)
        compared += 1
        if actual != expected:
            mismatches.append(f"{name}{args}: aot={actual!r} src={expected!r}")

    return {
        "module_ok": mc.module_ok,
        "fallback": sorted(mc.fallback_names),
        "compared": compared,
        "mismatches": mismatches,
    }


def op_attr_branches(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_attr_branches.py", "cratix_corpus_attr_branches", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "second_branch": [mod.read_in_second_branch(v) for v in (True, False)],
        "handlers": [mod.raise_in_second_handler(v) for v in (1, -1)],
        "two_branches": [mod.two_attr_branches(v) for v in (True, False)],
    }


def op_membership(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_membership.py", "cratix_corpus_membership", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "has": [mod.has_category(v) for v in ("work", "zzz")],
        "lacks": [mod.lacks_category(v) for v in ("work", "zzz")],
        "branch": [mod.branch_on_membership(v) for v in ("work", "zzz")],
        "number": [mod.has_number(v) for v in (2, 9)],
        "substring": [mod.has_substring(v) for v in ("world", "nope")],
        "chained": [mod.chained(v) for v in (2, 5)],
    }


def op_local_class(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_local_class.py", "cratix_corpus_local_class", out_dir)
    return {
        "module_ok": mc.module_ok,
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "local_only": mod.local_class_only(),
        "from_globals": mod.class_from_module_globals(),
        "capturing": mod.capturing_class_falls_back(),
    }


def op_match(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_match.py", "cratix_corpus_match", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "classify": [mod.classify(v) for v in (0, 1, 2, None, 7)],
        "guards": [mod.guard_case(v) for v in (0, 5, 20)],
        "block": [mod.with_block(v) for v in (0, 3, 8)],
        "nested": [mod.nested_match(0, True), mod.nested_match(0, False), mod.nested_match(1, True)],
    }


def op_module_forms(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_module_forms.py", "cratix_corpus_module_forms", out_dir)
    return {
        "module_ok": mc.module_ok,
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "root": mod.root_and_floor(9.0),
        "impl": mod.use_impl(),
        "builtins": mod.builtin_from_star(),
    }


def op_robust(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_robust.py", "cratix_corpus_robust", out_dir)
    return {
        "module_ok": mc.module_ok,
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "gen_inside": mod.gen_inside(),
        "broken_body": mod.use_broken_body(),
        "plain": mod.plain(4),
    }


def op_reraise(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_reraise.py", "cratix_corpus_reraise", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "ok_branch": mod.catch_outer(1),
        "rereased": mod.catch_outer(-1),
        "nested": mod.nested_reraise(7),
        "inner": mod.nested_class_use(),
    }


def op_modern(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_modern.py", "cratix_corpus_modern", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "walrus": mod.walrus([1, 2, 3]),
        "walrus_if": mod.walrus_in_if([1, 2, 3]),
        "delete": list(mod.delete_targets()),
        "multi": list(mod.multi_base()),
        "truth": list(mod.hasattr_truth()),
    }


def op_inherit(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_inherit.py", "cratix_corpus_inherit", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "raised": mod.raise_app(5),
        "total": mod.child_total(3),
        "label": mod.child_label(1),
        "is_exc": mod.is_exception(),
        "exc_args": list(mod.AppError(9).args),
    }


def op_module_level(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_module_level.py", "cratix_corpus_module_level", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "module_ok": mc.module_ok,
        "fallback_classes": [src.splitlines()[0] for _order, src in mc.fallback_classes],
        "limit": mod.limit_value(),
        "registry": mod.registry_size(),
        "caught": mod.raise_and_catch(7),
        "base_ok": mod.basename_of("/a/b.txt"),
        "dropped": mod.drop_temp(),
        "temp_gone": not hasattr(mod, "_TEMP"),
    }


def op_unpack(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_unpack.py", "cratix_corpus_unpack", out_dir)
    short = None
    try:
        mod.split_middle([1])
    except ValueError as exc:
        short = "ValueError"
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "head_rest": list(mod.head_rest([1, 2, 3])),
        "split_middle": list(mod.split_middle([1, 2, 3, 4])),
        "no_suffix": list(mod.no_suffix([9, 8])),
        "starred_only": mod.starred_only([1, 2, 3]),
        "swap": list(mod.swap([1, 2])),
        "nested": mod.nested_unpack([[1, 2], 3]),
        "short": short,
    }


def op_genexp_capture(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_genexp_capture.py", "cratix_corpus_genexp_capture", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "scaled": mod.scaled([1, 2, 3], 2),
        "filtered": mod.filtered([1, 5, 9], 4),
        "late": mod.late_binding([1, 2]),
        "nested": mod.nested_rows([[1, 2], [3]], 10),
        "global_scale": mod.uses_global([1, 2]),
        "plain": mod.no_capture([1, 2]),
    }


def op_module_decorators(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_module_decorators.py", "cratix_corpus_module_decorators", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "decorated": [name for name, _segs in mc.decorated_functions],
        "calls": list(mod.calls()),
        "cached": mod.cache_stats(),
        "fib": mod.fib(15),
    }


def op_closures(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_closures.py", "cratix_corpus_closures", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "adder": mod.make_adder(5)(10),
        "counter": mod.counter_factory(100)(7),
        "late": list(mod.uses_before_after([1, 2, 5, 20])),
        "nested": mod.nested_call([1, 2, 3]),
        "loop": mod.with_loop(4),
        "plain": mod.no_capture([1, 2]),
    }


def op_lambda(out_dir: Path) -> dict:
    mc, mod = _compile("corpus_lambda.py", "cratix_corpus_lambda", out_dir)
    return {
        "supported": sorted(mc.supported_names),
        "fallback": sorted(mc.fallback_names),
        "doubled": mod.doubled([1, 2]),
        "by_second": mod.by_second([[1, 9], [2, 3]]),
        "immediate": mod.immediate(),
        "with_global": mod.with_global(),
        "captures": mod.captures([1, 2], 10),
        "nested": mod.nested_lambda([1, 2]),
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
        elif op == "lambda":
            data = op_lambda(out_dir)
        elif op == "closures":
            data = op_closures(out_dir)
        elif op == "module_decorators":
            data = op_module_decorators(out_dir)
        elif op == "genexp_capture":
            data = op_genexp_capture(out_dir)
        elif op == "unpack":
            data = op_unpack(out_dir)
        elif op == "module_level":
            data = op_module_level(out_dir)
        elif op == "inherit":
            data = op_inherit(out_dir)
        elif op == "modern":
            data = op_modern(out_dir)
        elif op == "reraise":
            data = op_reraise(out_dir)
        elif op == "robust":
            data = op_robust(out_dir)
        elif op == "module_forms":
            data = op_module_forms(out_dir)
        elif op == "match":
            data = op_match(out_dir)
        elif op == "local_class":
            data = op_local_class(out_dir)
        elif op == "membership":
            data = op_membership(out_dir)
        elif op == "attr_branches":
            data = op_attr_branches(out_dir)
        elif op == "operators":
            data = op_operators(out_dir)
        elif op == "forms":
            data = op_forms(out_dir)
        elif op == "namespace":
            data = op_namespace(out_dir)
        elif op == "statements":
            data = op_statements(out_dir)
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