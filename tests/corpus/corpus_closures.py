"""语料：闭包 ✅（cell 字典 + functools.partial ✅）。"""


def make_adder(step):
    def adder(value):
        return value + step

    return adder


def counter_factory(start):
    def bump(amount):
        return start + amount

    return bump


def uses_before_after(values):
    limit = 10

    def keep(value):
        return value < limit

    small = [value for value in values if keep(value)]
    limit = 3
    tiny = [value for value in values if keep(value)]
    return len(small), len(tiny)


def nested_call(values):
    factor = 3

    def scale(value):
        return value * factor

    return sum(scale(value) for value in values)


def with_loop(n):
    total = 0

    def add(value):
        return value + total

    for i in range(n):
        total = total + 1
        _ = add(i)
    return add(0)


def no_capture(values):
    def double(value):
        return value * 2

    return [double(value) for value in values]
