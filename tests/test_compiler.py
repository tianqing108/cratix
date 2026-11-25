"""编译器前端与黄金模型的测试 ✅。

核心：**IR-VM 与源码行为一致** ✅ —— 这是 AOT 编译器语义安全网 ✅。
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler.golden import diff

CORPUS = ROOT / "tests" / "corpus"


# (文件, 函数名, [调用参数, ...]) ✅ 每个参数项是 (args, kwargs) 或 args ✅
CASES = [
    ("corpus_basic.py", "add", [(1, 2), (2.5, 0.5), ("a", "b")]),
    ("corpus_basic.py", "mixed", [(1,), (1, 5, 9, 9), ((1, 2, 3, 4), {"key": 10}), ((1,), {"zz": 1})]),
    ("corpus_basic.py", "control", [(0,), (7,), (12,)]),
    ("corpus_basic.py", "strings", [("bob", 3), ("a b", 12)]),
    ("corpus_basic.py", "containers", [([1, 2, 3],), ([],), ([-1, 0, 4, 5],)]),
    ("corpus_exceptions.py", "divide", [(6, 3), (1, 0)]),
    ("corpus_exceptions.py", "chained", [(2,), (0,), (-1,)]),
    ("corpus_exceptions.py", "custom_raise", [("key",), ("value",), ("none",)]),
    ("corpus_exceptions.py", "assertive", [(3,), (-1,)]),
    # with / for-else / while-else ✅
    ("corpus_with.py", "pure_with", [(True,), (False,)]),
    ("corpus_with.py", "pure_with_suppress", [()]),
    ("corpus_with.py", "pure_with_propagate", [()]),
    ("corpus_with.py", "pure_nested", [()]),
    ("corpus_with.py", "loop_else_break", [()]),
    ("corpus_with.py", "loop_else_no_break", [()]),
    ("corpus_with.py", "while_else_break", [()]),
    ("corpus_with.py", "while_else_no_break", [()]),
]


def _split(call):
    if isinstance(call, tuple) and len(call) == 2 and isinstance(call[1], dict):
        return call[0], call[1]
    return call, {}


class GoldenModelTest(unittest.TestCase):
    def test_source_matches_irvm(self):
        failures = []
        for filename, func, calls in CASES:
            path = CORPUS / filename
            for call in calls:
                args, kwargs = _split(call)
                result = diff.compare_function(path, func, args, kwargs)
                if not result["match"]:
                    failures.append((filename, func, call, result))
        if failures:
            detail = chr(10).join(
                "  %s %s%s -> src=%s irvm=%s" % (f, fn, a, r["source"], r["irvm"])
                for f, fn, a, r in failures
            )
            self.fail("IR-VM 与源码不一致：" + chr(10) + detail)

    def test_unsupported_are_rejected(self):
        path = CORPUS / "corpus_unsupported.py"
        tree = diff.module_tree(path)
        for name in ("gen", "closure", "uses_global"):
            supported, reason = diff.classify(tree, name)
            self.assertFalse(supported, name + " 应当被拒绝")
            self.assertTrue(reason, name + " 应当给出拒绝原因")

    def test_supported_are_accepted(self):
        for filename in ("corpus_basic.py", "corpus_exceptions.py", "corpus_with.py"):
            tree = diff.module_tree(CORPUS / filename)
            for stmt in tree.body:
                # **只看函数** ✅ —— 类的成员不在 classify 的范围里 ✅ ✅
                if isinstance(stmt, ast.FunctionDef):
                    supported, reason = diff.classify(tree, stmt.name)
                    self.assertTrue(supported, stmt.name + " 不应被拒绝：" + reason)


if __name__ == "__main__":
    unittest.main()