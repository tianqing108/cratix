"""整数流分析 ✅（`compiler/intflow.py`）—— 判定必须**保守** ✅。"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler.intflow import analyze_int_locals  # noqa: E402
from cratix.compiler.frontend.scope import analyze_function  # noqa: E402


def _safe(source: str):
    tree = ast.parse(source)
    func = tree.body[0]
    scope = analyze_function(func, set())
    return analyze_int_locals(func, scope.locals_)["safe"]


class IntFlowTest(unittest.TestCase):
    def test_accumulator_and_counter(self):
        source = '''
def loop(n):
    total = 0
    for i in range(n):
        total = total + i * 2
    return total
'''
        safe = _safe(source)
        self.assertIn("total", safe)
        self.assertIn("i", safe)

    def test_rejects_float(self):
        safe = _safe('''
def f():
    x = 1.5
    return x
''')
        self.assertEqual(safe, [])

    def test_rejects_string(self):
        safe = _safe('''
def f():
    x = "a"
    return x
''')
        self.assertEqual(safe, [])

    def test_rejects_true_division(self):
        safe = _safe('''
def f(a):
    x = a / 2
    return x
''')
        self.assertEqual(safe, [])

    def test_rejects_nondivergent_assign(self):
        safe = _safe('''
def f():
    x = 0
    x = "text"
    return x
''')
        self.assertEqual(safe, [])

    def test_rejects_parameter(self):
        safe = _safe('''
def f(a):
    b = a + 1
    return b
''')
        # a 是参数 -> 不在候选里；b 依赖 a -> 也不安全 ✅
        self.assertEqual(safe, [])

    def test_rejects_attribute_store(self):
        safe = _safe('''
def f(obj):
    x = 0
    obj.attr = x
    x = 1
    return x
''')
        self.assertNotIn("obj", safe)

    def test_allows_augassign(self):
        safe = _safe('''
def f():
    total = 0
    total += 2
    total *= 3
    return total
''')
        self.assertIn("total", safe)

    def test_rejects_big_literal(self):
        safe = _safe('''
def f():
    x = 0
    x = x + 2 ** 100
    return x
''')
        self.assertEqual(safe, [])


if __name__ == "__main__":
    unittest.main()
