"""语料：**常见形态** ✅（内建函数 ✅ / 容器方法 ✅ / 字符串方法 ✅ / 切片 ✅ /
推导式 ✅ / 解包 ✅ / f-string ✅ / 异常 ✅ / with ✅）。

和 `corpus_operators.py` 一样 ✅，是给**差分测试**用的 ✅：拿源码版的结果当基准 ✅。
"""


def builtins(values):
    return [
        len(values),
        abs(-7),
        min(values),
        max(values),
        sum(values),
        sorted(values),
        sorted(values, reverse=True),
        list(range(3)),
        list(enumerate(values)),
        list(zip(values, values)),
        any(values),
        all(values),
        isinstance(values, list),
        str(42),
        int("17"),
        float("2.5"),
        bool(values),
        repr("x"),
        ord("A"),
        chr(66),
        divmod(17, 5),
        reversed(values) and list(reversed(values)),
    ]


def string_methods(text):
    return [
        text.upper(),
        text.lower(),
        text.strip(),
        text.split(),
        "-".join(text.split()),
        text.replace("l", "L"),
        text.startswith("hel"),
        text.endswith("lo"),
        text.find("l"),
        text.count("l"),
        text.title(),
        text.zfill(12),
        text.center(14),
        text.capitalize(),
    ]


def list_methods(values):
    items = list(values)
    items.append(9)
    items.extend([10, 11])
    items.insert(0, 8)
    popped = items.pop()
    items.remove(9)
    found = items.index(10)
    counted = items.count(11)
    items.sort()
    items.reverse()
    copied = items.copy()
    copied.append(99)
    return items, popped, found, counted, len(copied)


def dict_methods(pairs):
    data = dict(pairs)
    data.setdefault("z", 26)
    keys = sorted(data.keys())
    values = [data[key] for key in keys]
    got = data.get("missing", -1)
    items_sorted = sorted(data.items())
    data.update({"w": 23})
    popped = data.pop("z")
    return keys, values, got, items_sorted, popped, sorted(data.keys())


def set_methods(values):
    left = set(values)
    right = set([3, 4, 5])
    return [
        sorted(left),
        sorted(left | right),
        sorted(left & right),
        sorted(left - right),
        left.issubset(left | right),
        len(left),
    ]


def slicing(values, text):
    return [
        values[1:3],
        values[:2],
        values[-2:],
        values[::2],
        values[::-1],
        text[0:2],
        text[-3:],
        text[:5],
        text[::-1],
    ]


def unpacking(values):
    first, second, *rest = values
    a = b = 0
    a, b = 1, 2
    (x, y), z = (3, 4), 5
    return first, second, rest, a, b, x, y, z


def comprehensions(values):
    squares = [v * v for v in values]
    even = [v for v in values if v % 2 == 0]
    mapping = {v: v + 1 for v in values}
    unique = {v % 3 for v in values}
    nested = [(i, j) for i in range(2) for j in range(2)]
    gen_total = sum(v for v in values if v > 2)
    return squares, even, sorted(mapping.items()), sorted(unique), nested, gen_total


def fstrings(name, count, value):
    return [
        f"{name}-{count}",
        f"{count:03d}",
        f"{value:.2f}",
        f"{name!r}",
        f"{name:>6}",
        f"{name:<6}",
        "%s=%d" % (name, count),
    ]


def exception_flow(values):
    total = 0
    try:
        for item in values:
            if item < 0:
                raise ValueError("negative: " + str(item))
            total += item
    except ValueError as exc:
        return "err:" + str(exc), total
    else:
        return "ok", total
    finally:
        total += 100


def try_finally(values):
    total = 0
    try:
        total += 1
        return total
    finally:
        total += 2


def loops(values):
    total = 0
    for item in values:
        if item == 0:
            continue
        if item > 10:
            break
        total += item
    count = 0
    while count < 3:
        count += 1
    return total, count


class Counter:
    def __init__(self):
        self.value = 0
        self.history = []

    def add(self, delta):
        self.value += delta
        self.history.append(self.value)
        return self.value

    def total(self):
        return sum(self.history)


def with_class(values):
    counter = Counter()
    for item in values:
        counter.add(item)
    return counter.value, counter.total(), counter.history


def use_with(values):
    class Guard:
        def __init__(self):
            self.entered = 0
            self.exited = 0

        def __enter__(self):
            self.entered += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            self.exited += 1
            return False

    guard = Guard()
    with guard as inner:
        inner.entered += 10
    return guard.entered, guard.exited, len(values)
