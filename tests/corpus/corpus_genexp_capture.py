"""语料：生成器表达式**捕获外层局部** ✅（cells + AST 重写 ✅）。"""


def scaled(values, factor):
    return sum(value * factor for value in values)


def filtered(values, floor):
    return [value for value in values if value > floor]


def late_binding(values):
    offset = 1
    gen = (value + offset for value in values)
    offset = 10
    return list(gen)


def nested_rows(rows, bonus):
    return sum(bonus + item for row in rows for item in row)


def uses_global(values):
    return sum(value * SCALE for value in values)


SCALE = 3


def no_capture(values):
    return sum(value + 1 for value in values)
