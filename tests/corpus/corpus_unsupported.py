"""语料：**必须被拒绝**的构造 ✅（验证回退判定 ✅）。"""


def gen(n):
    for i in range(n):
        yield i


def closure(n):
    total = 0

    def inner(x):
        return total + x

    return inner


def uses_global():
    global COUNTER
    COUNTER += 1
    return COUNTER


COUNTER = 0