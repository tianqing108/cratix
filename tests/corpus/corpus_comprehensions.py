"""语料：推导式 ✅（独立作用域 ✅ / 多生成器 ✅ / 解包 ✅ / 嵌套 ✅）。"""


def single(items):
    return [x * x for x in items if x > 0]


def multi(items):
    return [(a, b) for a in items for b in items if a < b]


def nested(rows):
    return [[y * 2 for y in row] for row in rows]


def unpack(pairs):
    return [a + b for a, b in pairs]


def dict_multi(data):
    return {k: [k + str(v) for v in vals] for k, vals in data.items()}


def scoped(items):
    x = 100
    result = [x for x in items]
    return result, x


def self_iter():
    x = [1, 2, 3]
    return [x for x in x]


def setcomp_cond(items):
    return {x % 3 for x in items if x % 3}


def filtered_multi(items):
    return [a * b for a in items if a > 1 for b in items if b < 4]


def shadowed(items):
    x = "outer"
    rows = [[x for x in items] for _ in range(2)]
    return rows, x


def str_join(words):
    return "-".join([w.upper() for w in words if len(w) > 1])
