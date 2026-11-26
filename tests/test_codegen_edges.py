"""**生成期边界**回归测试 ✅ —— 都是"真实第三方包"抓出来的 bug ✅。

这些形状标准库体检**看不到** ✅，但真实项目里很常见 ✅：
1. 类里同名方法（`@property` + `@x.setter` ✅）
2. 超长字符串字面量（MSVC `C2026` ✅）
3. 同名顶级函数（`if/elif/else` 各定义一个 `open` ✅）
4. `finally` 体里再套 `try`（标签被复制多份 ✅）
5. 方法描述符 + 参数不叫 `self`（`cratix_shifted` 声明/清理漂移 ✅）
6. 类创建函数里用到 interned 属性名（`C2065` ✅）

每个用例都是：**编得过 + 跑得对** ✅（只编得过不算 ✅）。
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.compiler import driver, loader  # noqa: E402


class _Base(unittest.TestCase):
    SOURCE = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cratix_edge_"))
        source = cls.tmp / "edge_mod.py"
        source.write_text(textwrap.dedent(cls.SOURCE), encoding="utf-8")
        cls.mc = driver.compile_module(source, "edge_mod")
        assert cls.mc.module_ok, cls.mc.module_reason
        cls.mod = None
        try:
            pyd = driver.build_pyd(cls.mc, cls.tmp)
            cls.mod = loader.load_compiled_module(pyd, cls.mc.init_symbol)
        except BaseException as exc:  # noqa: BLE001 - 编译失败要让用例报出来
            raise AssertionError("C 编译失败：" + str(exc)[:400]) from None


class DuplicateMethodTest(_Base):
    SOURCE = '''
class Box:
    def __init__(self):
        self._value = 1

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new):
        self._value = new


def use():
    box = Box()
    box.value = 5
    return box.value
'''

    def test_property_and_setter(self):
        self.assertEqual(self.mod.use(), 5)


class LongStringTest(_Base):
    SOURCE = '''
LONG = "x" * 200 + "y" * 4000 + "z" * 9000


def use():
    return len(LONG), LONG[:1], LONG[-1:]
'''

    def test_long_literal(self):
        length, head, tail = self.mod.use()
        self.assertEqual(length, 13200)
        self.assertEqual(head, "x")
        self.assertEqual(tail, "z")


class SameNameDefTest(_Base):
    SOURCE = '''
MODE = "b"

if MODE == "a":
    def pick():
        return "a"
elif MODE == "b":
    def pick():
        return "b"
else:
    def pick():
        return "c"


def use():
    return pick()
'''

    def test_last_definition_wins(self):
        self.assertEqual(self.mod.use(), "b")


class TryInFinallyTest(_Base):
    SOURCE = '''
def work(values):
    total = 0
    try:
        for value in values:
            total = total + value
    finally:
        try:
            total = total + 1
        except Exception:
            total = -1
    return total
'''

    def test_try_inside_finally(self):
        self.assertEqual(self.mod.work([1, 2, 3]), 7)


class DescriptorNotSelfTest(_Base):
    SOURCE = '''
class Holder:
    def __init__(self):
        self.items = [1, 2]

    def total(items_self):
        return len(items_self.items)


def use():
    return Holder().total()
'''

    def test_first_param_not_named_self(self):
        self.assertEqual(self.mod.use(), 2)


class MakerAttrNameTest(_Base):
    SOURCE = '''
class Config:
    local = 1
    remote = 2

    def pick(self, local):
        return self.local + local


def use():
    return Config().pick(10)
'''

    def test_maker_interned_attr(self):
        self.assertEqual(self.mod.use(), 11)


if __name__ == "__main__":
    unittest.main()
