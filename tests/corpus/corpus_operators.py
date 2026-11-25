"""语料：**全部运算符** ✅ —— 用**差分测试**对照源码版 ✅（不写死期望值 ✅）。

为什么要这样 ✅：`in` / `not in` 的极性曾经整个写反 ✅（`x in Y` 恒为 False ✅），
而当时**没有任何测试覆盖它** ✅ —— 因为语料里没人用过 `in` ✅。
这个文件把运算符**排一遍** ✅，测试端拿源码版的结果当基准 ✅。
"""


def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b


def truediv(a, b):
    return a / b


def floordiv(a, b):
    return a // b


def mod(a, b):
    return a % b


def power(a, b):
    return a ** b


def lshift(a, b):
    return a << b


def rshift(a, b):
    return a >> b


def bitand(a, b):
    return a & b


def bitor(a, b):
    return a | b


def bitxor(a, b):
    return a ^ b


def invert(a):
    return ~a


def negate(a):
    return -a


def positive(a):
    return +a


def logical_not(a):
    return not a


def logical_and(a, b):
    return a and b


def logical_or(a, b):
    return a or b


def contains(a, b):
    return a in b


def not_contains(a, b):
    return a not in b


def identity(a, b):
    return a is b


def not_identity(a, b):
    return a is not b


def lt(a, b):
    return a < b


def le(a, b):
    return a <= b


def eq(a, b):
    return a == b


def ne(a, b):
    return a != b


def ge(a, b):
    return a >= b


def gt(a, b):
    return a > b


def chained_range(a, b, c):
    return a < b < c


def chained_mixed(a, b, c):
    return a <= b and b != c


def chained_contains(a, b, c):
    return a < b and b in c


def ternary(flag, a, b):
    return a if flag else b


def walrus_in_condition(values):
    total = 0
    while (piece := len(values)) > 0:
        total = total + piece
        values = values[1:]
    return total
