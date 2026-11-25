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
        self.assertEqual(data["fallback"], ["closure", "gen", "uses_global"])
        self.assertEqual(data["embedded"], ["closure", "gen", "uses_global"])

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
        self.assertEqual(data["fallback"], ["composed", "double", "tag", "twice"])
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

    def test_03_fallback_functions_work_at_runtime(self):
        data = run_worker("fallback")
        self.assertEqual(data["gen"], [0, 1, 2])
        self.assertEqual(data["uses_global"], 1)
        self.assertEqual(data["closure"], 2)