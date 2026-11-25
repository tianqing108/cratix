"""**helper 取用后的引用计数** ✅ —— 防 #164 那个 P0 回归 ✅。

背景 ✅：从 helper 命名空间取函数用的是 `PyDict_GetItemString`（**借用引用** ✅），
以前直接塞进**会被清理的临时变量** ✅ -> 每调用一次少一个引用 ✅ -> **第二次调用**就炸 ✅
（报的还是 `list.sort` 的 TypeError ✅，完全指不到真凶 ✅）。

所以这里**必须调用两次以上** ✅ —— 只调一次是发现不了的 ✅。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler import driver, loader  # noqa: E402

SOURCE = '''
def lambda_key(values):
    return sorted(values, key=lambda item: item)


def lambda_tuple_key(values):
    return sorted(values, key=lambda item: (len(item), item))


def inner_helper(base):
    def add(value):
        return value + base
    return add(1) + add(2)


def genexp_scale(scale):
    return sum(x * scale for x in (1, 2, 3))


def dict_roundtrip(text):
    counts = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    return counts


def string_append(parts):
    text = ""
    for part in parts:
        text = text + part
    return text
'''


class HelperRefTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile

        cls.tmp = Path(tempfile.mkdtemp(prefix="cratix_refs_"))
        source = cls.tmp / "refs_mod.py"
        source.write_text(SOURCE, encoding="utf-8")
        mc = driver.compile_module(source, "refs_mod")
        assert mc.module_ok, mc.module_reason
        pyd = driver.build_pyd(mc, cls.tmp)
        cls.mod = loader.load_compiled_module(pyd, mc.init_symbol)

    def test_lambda_key_repeated_calls(self):
        # **至少 3 次** ✅ —— 借用引用少 1 的错误第 2 次才现形 ✅
        for _ in range(3):
            self.assertEqual(self.mod.lambda_key(["b", "a"]), ["a", "b"])

    def test_lambda_tuple_key_repeated_calls(self):
        for _ in range(3):
            self.assertEqual(self.mod.lambda_tuple_key(["bbb", "a", "cc"]), ["a", "cc", "bbb"])

    def test_inner_def_repeated_calls(self):
        for _ in range(3):
            self.assertEqual(self.mod.inner_helper(5), 13)

    def test_genexp_repeated_calls(self):
        for _ in range(3):
            self.assertEqual(self.mod.genexp_scale(2), 12)

    def test_dict_fast_paths(self):
        self.assertEqual(self.mod.dict_roundtrip("a b a"), {"a": 2, "b": 1})
        for _ in range(3):
            self.assertEqual(self.mod.dict_roundtrip("x x y"), {"x": 2, "y": 1})

    def test_string_append_repeated(self):
        self.assertEqual(self.mod.string_append(["a", "b"]), "ab")
        for _ in range(3):
            self.assertEqual(self.mod.string_append(["x", "y", "z"]), "xyz")


if __name__ == "__main__":
    unittest.main()
