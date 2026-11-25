"""AOT 测试 ✅ —— 在**独立子进程**里跑 ✅。

理由 ✅：同一进程里连续加载多个 AOT 模块会破坏解释器状态 ✅
（3.14 的自适应字节码缓存 ✅ —— "Executing a cache" ✅）。
产品路径不受影响 ✅（一个应用只加载自己的模块 ✅）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests" / "aot_worker.py"


def run_worker(op: str) -> dict:
    """在独立子进程里跑一个 AOT 操作 ✅ —— 返回结果 JSON ✅。"""
    out_dir = ROOT / "build" / ("aot_" + op)
    proc = subprocess.run(
        [sys.executable, str(WORKER), op, str(out_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise AssertionError("AOT 子进程失败：" + proc.stderr[-1500:])
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("AOT 子进程输出异常：" + proc.stdout[-800:] + proc.stderr[-800:]) from exc
    if not data.get("ok"):
        raise AssertionError("AOT 子进程报错：" + str(data.get("error")))
    return data

class FunctionFallbackTest(unittest.TestCase):
    def test_01_unsupported_functions_are_embedded(self):
        data = run_worker("fallback")
        # **global 现在能 AOT 了** ✅（只剩闭包与生成器回退 ✅）
        # **闭包现在能 AOT 了** ✅（cell 字典 + functools.partial ✅）-> 只剩生成器回退 ✅
        self.assertEqual(data["fallback"], ["gen"])
        self.assertEqual(data["embedded"], ["gen"])

    def test_02_try_except_is_supported(self):
        data = run_worker("try")
        self.assertIn("safe_div", data["supported"])
        self.assertEqual(data["div"], 2.0)
        self.assertIsNone(data["div_zero"])
        self.assertIsNone(data["div2_zero"])
        self.assertEqual(data["div2_type"], "type")
        self.assertEqual(data["nested"], "inner")

    def test_04_with_statement_is_supported(self):
        data = run_worker("with")
        self.assertEqual(data["fallback"], [], "with 语料不应回退")
        for name in (
            "simple", "nested_exit_order", "suppress_exc", "propagate_exc",
            "return_inside", "break_inside_with", "continue_inside_with",
            "with_in_try", "try_in_with", "loop_else_break",
            "loop_else_no_break", "while_else_break", "while_else_no_break",
            "pure_with_suppress", "pure_with_propagate", "pure_with", "pure_nested",
        ):
            self.assertIn(name, data["supported"], name + " 应当被 AOT 化")

    def test_05_with_semantics_match_cpython(self):
        data = run_worker("with")
        # 进入 / 退出顺序 ✅
        self.assertEqual(data["simple"], "enter:a|body:a|exit:a")
        self.assertEqual(data["nested"], "enter:a|enter:b|body:ab|exit:b|exit:a")
        # 异常被抑制 ✅ -> 继续执行 ✅
        self.assertEqual(data["suppress"], "enter:a|before|exit:a:ValueError|after")
        # 异常不被抑制 ✅ -> 向外传播 ✅
        self.assertEqual(data["propagate"], "enter:a|exit:a:KeyError|caught")
        # return / break / continue 都要先跑 __exit__ ✅
        self.assertEqual(data["return_inside"], "ret:a")
        self.assertEqual(data["break_inside"], 1)
        self.assertEqual(data["continue_inside"], 5)
        self.assertEqual(data["with_in_try"], "enter:a|exit:a:ZeroDivisionError|handled")
        self.assertEqual(data["try_in_with"], "enter:a|inner|exit:a")
        # for/while 的 else ✅ —— 只有**没 break** 才跑 ✅
        self.assertEqual(data["loop_else_break"], [0])
        self.assertEqual(data["loop_else_no_break"], [0, 1, 2, "else"])
        self.assertEqual(data["while_else_break"], [0, 1])
        self.assertEqual(data["while_else_no_break"], [0, 1, "else"])
        # 纯函数 ✅
        self.assertEqual(data["pure_suppress"], "suppressed")
        self.assertEqual(data["pure_propagate"], "caught")
        self.assertEqual(data["pure_with"], 11)
        self.assertEqual(data["pure_nested"], 3)

    def test_06_decorators_and_property(self):
        data = run_worker("decorators")
        # 装饰器函数本身回退 ✅（体内有嵌套 def ✅）；被装饰的函数也回退 ✅（PyCFunction 没有 __dict__ ✅）
        # **模块级装饰器也支持了** ✅（跳板 + 套装饰器 + 换回公开名 ✅）-> 全部 AOT ✅
        self.assertEqual(data["fallback"], [])
        # 类方法全部 AOT 化 ✅（含 @property / @staticmethod / @classmethod / 自定义装饰器 ✅）
        for name in ("doubled", "label", "helper", "build", "scaled", "plain"):
            self.assertIn(name, data["supported"], name + " 应当被 AOT 化")
        # 与源码逐字一致 ✅
        self.assertEqual(data["module_level"], "(6, 'tag(6)')")
        self.assertEqual(data["box_ops"], "(10, 'box:5', 2, 7, 10, 5)")
        self.assertEqual(data["marked_ops"], "(True, 1)")

    def test_07_comprehensions(self):
        data = run_worker("comprehensions")
        # **多生成器 / 解包 / 嵌套都能 AOT** ✅（原来是"整函数回退" ✅）
        for name in (
            "single", "multi", "nested", "unpack", "dict_multi", "scoped",
            "self_iter", "setcomp_cond", "filtered_multi", "shadowed", "str_join",
        ):
            self.assertIn(name, data["supported"], name + " 应当被 AOT 化")
        # 与源码逐字一致 ✅
        self.assertEqual(data["single"], "[9, 4]")
        self.assertEqual(data["multi"], "[(1, 2), (1, 3), (2, 3)]")
        self.assertEqual(data["nested"], "[[2, 4], [6]]")
        self.assertEqual(data["unpack"], "[3, 7]")
        self.assertEqual(data["dict_multi"], "{'a': ['a1', 'a2']}")
        # **独立作用域** ✅：外层的 x 不被推导式改写 ✅
        self.assertEqual(data["scoped"], "([1, 2], 100)")
        # 最外层可迭代对象用外层作用域求值 ✅
        self.assertEqual(data["self_iter"], "[1, 2, 3]")
        self.assertEqual(data["setcomp_cond"], "{1, 2}")
        self.assertEqual(data["filtered_multi"], "[2, 4, 6, 3, 6, 9]")
        self.assertEqual(data["shadowed"], "([[1, 2], [1, 2]], 'outer')")
        self.assertEqual(data["str_join"], "'BB-CCC'")

    def test_08_loop_temporaries_do_not_leak(self):
        """**每轮都要释放临时变量** ✅ —— 否则长循环里引用线性增长 ✅（实测过 ✅）。"""
        data = run_worker("leaks")
        for name in (
            "loop_refs", "comp_refs", "with_continue", "with_break", "try_refs",
            "call_refs", "call_fn_refs",
        ):
            self.assertEqual(
                data[name + "_delta"], 0,
                name + " 泄漏了引用：delta=" + str(data[name + "_delta"]),
            )
        # 行为不变 ✅
        self.assertEqual(data["loop_refs_value"], 200)
        self.assertEqual(data["comp_refs_value"], 50)
        self.assertEqual(data["with_continue_value"], 50)
        self.assertEqual(data["with_break_value"], 1)
        self.assertEqual(data["try_refs_value"], 50)  # 100 轮里偶数 50 个
        # **调用参数** ✅：计数都是 0 ✅（obj 不在 [1,2,3] 里 ✅）
        self.assertEqual(data["call_refs_value"], 0)
        self.assertEqual(data["call_fn_refs_value"], 100)  # take_two 每次都返回 1

    def test_09_metadata_is_faithful(self):
        """**元数据保真** ✅：docstring / 签名 / qualname / module 与源码逐字段一致 ✅。"""
        data = run_worker("metadata")
        if data["mismatches"]:
            detail = chr(10).join(
                str(item["key"]) + ": src=" + str(item["src"]) + " aot=" + str(item["aot"])
                for item in data["mismatches"]
            )
            self.fail("元数据与源码不一致：" + chr(10) + detail)
        self.assertGreaterEqual(data["fields"], 8)

    def test_10_generator_expressions(self):
        """生成器表达式 ✅ —— **真惰性** ✅，且引用外层局部变量时整函数回退 ✅。"""
        data = run_worker("genexp")
        # 只有"引用外层局部变量"的那个回退 ✅
        # **生成器表达式捕获也支持了** ✅ -> 全部 AOT ✅（晚绑定 ✅）
        self.assertEqual(data["fallback"], [])
        for name in ("total", "filtered", "nested", "lazy", "passthrough", "shadowed", "result"):
            self.assertIn(name, data["supported"], name + " 应当被 AOT 化")
        self.assertTrue(data["helpers"], "应当合成生成器辅助函数")
        # 值与源码一致 ✅
        self.assertEqual(data["total"], 9)
        self.assertEqual(data["filtered"], 7)
        self.assertEqual(data["nested"], 6)
        # **惰性证据** ✅：range(10000000) 只取前 3 个 ✅（先算完会跑很久 ✅）
        self.assertEqual(data["lazy"], [0, 1, 4])
        self.assertEqual(data["passthrough"], [0, 1, 2, 3])
        self.assertEqual(data["shadowed"], [3, "outer"])
        self.assertEqual(data["captured"], 23)
        self.assertEqual(data["holder"], 6)

    def test_11_self_method_calls(self):
        """**self 方法直调** ✅：安全前提不满足时必须退回属性查找 ✅。"""
        data = run_worker("methods")
        self.assertEqual(data["fallback"], [])
        # 值必须与源码一致 ✅
        self.assertEqual(data["self_calls"], sum(1 + i for i in range(10)))
        self.assertEqual(data["shadow_calls"], sum(10 - i for i in range(10)))
        self.assertEqual(data["attribute_reads"], sum(2 + 4 for _ in range(10)))
        # **直调生效** ✅（`run` 里 `self.step(...)` 被编成直接调用 ✅）
        self.assertTrue(data["direct_used"], "self.step 应当走直调")
        # **被实例属性覆盖过的方法不许直调** ✅（否则会绕过覆盖 ✅）
        self.assertFalse(data["shadow_direct"], "被覆盖的 self.step 不应直调")

    def test_12_try_else_finally(self):
        """try/else + try/finally ✅ —— 正常 / 异常 / return / break / continue 五条路都要跑 finally ✅。"""
        data = run_worker("finally")
        values = data["values"]
        self.assertEqual(values["simple"], "body|finally")
        self.assertEqual(values["with_exception"], "body|finally|caught")
        self.assertEqual(values["with_return"], ["ret", "body|finally"])
        self.assertEqual(values["with_break"], "iter0|fin0|iter1|fin1")
        self.assertEqual(values["with_continue"], "iter0|fin0|fin1|iter2|fin2")
        self.assertEqual(values["else_runs"], "body|else")
        self.assertEqual(values["else_skipped"], "body|caught")
        self.assertEqual(values["nested"], "inner|fin-inner|caught|fin-outer")
        self.assertEqual(values["value_after"], 44)
        # 只有"带嵌套 def"的那个回退 ✅（闭包不支持 ✅）
        # 嵌套 def 不再需要回退 ✅（没有捕获 ✅）
        self.assertEqual(data["fallback"], [])

    def test_13_imports_globals_augassign(self):
        """`from ... import` ✅ / `global` ✅ / 非局部复合赋值 ✅ —— 真实代码最常见的三件事 ✅。"""
        data = run_worker("extras")
        self.assertTrue(data["module_ok"], "有 from-import 的模块必须能编译")
        self.assertEqual(data["fallback"], [])
        self.assertEqual(data["counter"], 5)
        self.assertEqual(data["accumulate"], 17)
        self.assertEqual(data["local_import"], "y.txt:2")
        self.assertEqual(data["imported"], "a|1")
        self.assertEqual(data["reset"], 0)
        self.assertEqual(data["after_reset"], 0)

    def test_14_lambda(self):
        """lambda ✅ —— 编译成模块级原生函数；**捕获外层局部**的回退 ✅。"""
        data = run_worker("lambda")
        # **lambda 捕获也支持了** ✅（与嵌套 def 同一套 cell 机制 ✅）-> 全部 AOT ✅
        self.assertEqual(data["fallback"], [])
        for name in ("doubled", "by_second", "immediate", "with_global", "nested_lambda"):
            self.assertIn(name, data["supported"], name + " 应当被 AOT 化")
        self.assertEqual(data["doubled"], [2, 4])
        # **关键字调用** ✅：`sorted(pairs, key=lambda ...)` —— 顺带守住 vectorcall 的 nargsf ✅
        self.assertEqual(data["by_second"], [[2, 3], [1, 9]])
        self.assertEqual(data["immediate"], 12)
        self.assertEqual(data["with_global"], 11)
        self.assertEqual(data["captures"], [11, 12])
        self.assertEqual(data["nested"], [3, 6])

    def test_15_closures(self):
        """闭包 ✅ —— cell 字典 + functools.partial ✅；**晚绑定**必须正确 ✅。"""
        data = run_worker("closures")
        for name in ("make_adder", "counter_factory", "uses_before_after", "with_loop", "no_capture"):
            self.assertIn(name, data["supported"], name + " 应当被 AOT 化")
        self.assertEqual(data["adder"], 15)
        self.assertEqual(data["counter"], 107)
        # **晚绑定** ✅：limit 从 10 改成 3 之后，同一个闭包看到的是新值 ✅
        self.assertEqual(data["late"], [3, 2])
        self.assertEqual(data["loop"], 4)
        self.assertEqual(data["plain"], [2, 4])

    def test_16_module_decorators(self):
        """模块级装饰器 ✅（`@lru_cache` 真的在缓存 ✅ / 自定义装饰器 ✅ / 叠两层 ✅）。"""
        data = run_worker("module_decorators")
        self.assertEqual(data["fallback"], [])
        self.assertEqual(sorted(data["decorated"]), ["fib", "plus_one", "stacked"])
        self.assertEqual(data["calls"], [10, "[12]", 55])
        self.assertTrue(data["cached"], "lru_cache 必须真的在缓存")
        self.assertEqual(data["fib"], 610)

    def test_17_genexp_captures(self):
        """生成器表达式**捕获外层局部** ✅（cells + AST 重写 ✅，晚绑定 ✅）。"""
        data = run_worker("genexp_capture")
        self.assertEqual(data["fallback"], [])
        self.assertEqual(data["scaled"], 12)
        self.assertEqual(data["filtered"], [5, 9])
        # **晚绑定** ✅：创建后把 offset 改成 10，生成器看到的是新值 ✅
        self.assertEqual(data["late"], [11, 12])
        self.assertEqual(data["nested"], 36)
        self.assertEqual(data["global_scale"], 9)
        self.assertEqual(data["plain"], 5)

    def test_03_fallback_functions_work_at_runtime(self):
        data = run_worker("fallback")
        self.assertEqual(data["gen"], [0, 1, 2])
        self.assertEqual(data["uses_global"], 1)
        self.assertEqual(data["closure"], 2)