"""语料：`from ... import` ✅ / `global` ✅ / 非局部复合赋值 ✅。"""

from os.path import basename
from os import sep
from collections import OrderedDict

COUNTER = 0


class Counter:
    def __init__(self):
        self.value = 0

    def bump(self, amount):
        self.value += amount
        return self.value


def counter():
    item = Counter()
    item.bump(2)
    item.bump(3)
    return item.value


def accumulate():
    bucket = {"a": 1, "b": 2}
    bucket["a"] += 10
    bucket["b"] *= 3
    return bucket["a"] + bucket["b"]


def local_import():
    from os.path import basename as base
    from math import floor

    return base("/x/y.txt") + ":" + str(floor(2.7))


def imported():
    # **不要用 sep 的具体字符** ✅（Windows 是 \ ✅、POSIX 是 / ✅）—— 用长度 ✅
    return basename("/p/a") + "|" + str(len(sep))


def bump_counter(amount):
    global COUNTER
    COUNTER = COUNTER + amount
    return COUNTER


def read_counter():
    return COUNTER


def reset_counter():
    global COUNTER
    COUNTER = 0
    return COUNTER


def ordered():
    data = OrderedDict()
    data["k"] = 1
    return len(data)
