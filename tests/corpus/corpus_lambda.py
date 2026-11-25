"""语料：lambda ✅（无捕获编译成原生函数；有捕获则整函数回退 ✅）。"""


def doubled(values):
    return list(map(lambda x: x * 2, values))


def by_second(pairs):
    return sorted(pairs, key=lambda pair: pair[1])


def immediate():
    fn = lambda a, b=2: a + b
    return fn(3) + fn(3, 4)


def with_global():
    return (lambda x: x + GLOBAL_OFFSET)(1)


GLOBAL_OFFSET = 10


def captures(values, offset):
    """**捕获外层局部** ✅ -> 整函数回退 ✅（语义正确 ✅）。"""
    return list(map(lambda x: x + offset, values))


def nested_lambda(values):
    return list(map(lambda x: (lambda y: y * 3)(x), values))
