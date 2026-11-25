"""语料：with ✅（上下文管理器语义）。"""

LOG = []


class Ctx:
    def __init__(self, name, suppress=False):
        self.name = name
        self.suppress = suppress

    def __enter__(self):
        LOG.append("enter:" + self.name)
        return self.name

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            LOG.append("exit:" + self.name)
        else:
            LOG.append("exit:" + self.name + ":" + exc_type.__name__)
        return self.suppress


def simple():
    with Ctx("a") as value:
        LOG.append("body:" + value)
    return "|".join(LOG)


def nested_exit_order():
    with Ctx("a") as x, Ctx("b") as y:
        LOG.append("body:" + x + y)
    return "|".join(LOG)


def suppress_exc():
    with Ctx("a", True) as value:
        LOG.append("before")
        raise ValueError("boom")
    LOG.append("after")
    return "|".join(LOG)


def propagate_exc():
    try:
        with Ctx("a") as value:
            raise KeyError("k")
        return "not-reached"
    except KeyError:
        LOG.append("caught")
        return "|".join(LOG)


def return_inside():
    with Ctx("a") as value:
        return "ret:" + value


def break_inside_with():
    total = 0
    for index in range(5):
        with Ctx("i" + str(index)) as value:
            total = total + index
            if index == 1:
                break
    return total


def continue_inside_with():
    total = 0
    for index in range(4):
        with Ctx("c" + str(index)) as value:
            if index == 1:
                continue
            total = total + index
    return total


def with_in_try():
    try:
        with Ctx("a") as value:
            raise ZeroDivisionError("z")
    except ZeroDivisionError:
        LOG.append("handled")
    return "|".join(LOG)


def try_in_with():
    with Ctx("a") as value:
        try:
            raise TypeError("t")
        except TypeError:
            LOG.append("inner")
    return "|".join(LOG)


def loop_else_break():
    seen = []
    for index in range(3):
        seen.append(index)
        break
    else:
        seen.append("else")
    return seen


def loop_else_no_break():
    seen = []
    for index in range(3):
        seen.append(index)
    else:
        seen.append("else")
    return seen


def while_else_no_break():
    count = 0
    seen = []
    while count < 2:
        seen.append(count)
        count = count + 1
    else:
        seen.append("else")
    return seen


class Pure:
    """无日志的上下文管理器 ✅ —— 黄金模型差分要用**纯函数** ✅（不依赖模块级状态 ✅）。"""

    def __init__(self, value, suppress=False):
        self.value = value
        self.suppress = suppress

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return self.suppress


def pure_with(flag):
    with Pure(10) as value:
        if flag:
            return value + 1
    return -1


def pure_with_suppress():
    with Pure(7, True) as value:
        raise ValueError("boom")
    return "suppressed"


def pure_with_propagate():
    try:
        with Pure(7) as value:
            raise KeyError("k")
    except KeyError:
        return "caught"
    return "missed"


def pure_nested():
    with Pure(1) as a, Pure(2) as b:
        return a + b


def while_else_break():
    count = 0
    seen = []
    while count < 5:
        seen.append(count)
        if count == 1:
            break
        count = count + 1
    else:
        seen.append("else")
    return seen
