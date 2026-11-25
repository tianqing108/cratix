"""语料：生成器表达式 ✅（真惰性 ✅ 不是先算完再迭代 ✅）。"""


def total(items):
    return sum(x + 1 for x in items)


def filtered(items):
    return sum(x for x in items if x > 2)


def nested(rows):
    return sum(y for row in rows for y in row)


def lazy(items):
    """**惰性证据** ✅：无限序列也能取前 3 个 ✅（先算完就会卡死 ✅）。"""
    return [x for x, _ in zip((i * i for i in items), range(3))]


def passthrough():
    source = (x for x in range(4))
    return list(source)


def shadowed(items):
    x = "outer"
    total_value = sum(len(str(v)) for v in items)
    return total_value, x


def captured(items, offset):
    """**引用外层局部变量** ✅ -> 合不出模块级生成器函数 ✅ -> 整函数回退 ✅（语义仍然正确 ✅）。"""
    return sum(x + offset for x in items)


class Holder:
    def __init__(self, values):
        self.values = values

    def result(self):
        return sum(v * 2 for v in self.values)
