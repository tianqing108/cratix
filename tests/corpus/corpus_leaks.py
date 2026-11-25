"""语料：临时变量 / 辅助变量的**引用释放** ✅（长循环不能泄漏 ✅）。"""


class Ctx:
    def __init__(self):
        self.value = 1

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return False


def loop_refs(obj, n):
    total = 0
    for index in range(n):
        last = obj
        total = total + 1
    return total


def comp_refs(obj, n):
    items = [x for x in range(n)]
    out = [obj for _ in items]
    return len(out)


def with_continue(obj, n):
    total = 0
    for index in range(n):
        with Ctx() as value:
            last = obj
            if index % 2 == 0:
                continue
            total = total + value
    return total


def with_break(obj, n):
    total = 0
    for index in range(n):
        with Ctx() as value:
            last = obj
            total = total + value
            break
    return total


def try_refs(obj, n):
    total = 0
    for index in range(n):
        try:
            last = obj
            if index % 2 == 0:
                raise ValueError("even")
        except ValueError:
            total = total + 1
    return total
