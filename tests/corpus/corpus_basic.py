"""语料：基础表达式与控制流 ✅。"""


def add(a, b):
    return a + b


def mixed(a, b=2, *rest, key=None, **kw):
    total = a + b
    total += len(rest)
    if key is not None:
        total += key
    for name in sorted(kw):
        total += len(name)
    return total


def control(n):
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(i)
        elif i % 3 == 0:
            continue
        else:
            out.append(-i)
    else:
        out.append("done")
    while n > 0:
        n -= 1
        if n == 3:
            break
    return out


def strings(name, count):
    label = f"hello {name!r} x{count:03d}"
    parts = label.split(" ")
    joined = "-".join(parts)
    return joined.upper()


def containers(items):
    data = {"a": 1, "b": 2}
    data.update({"c": 3})
    total = sum(data.values())
    squares = [x * x for x in items if x > 0]
    mapping = {k: len(k) for k in sorted(data)}
    unique = {x % 3 for x in items}
    first, *rest = items
    return total, squares, mapping, sorted(unique), first, rest