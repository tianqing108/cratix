"""语料：异常与控制流边界 ✅。"""


def divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None
    finally:
        pass


def chained(x):
    try:
        if x < 0:
            raise ValueError("negative")
        return 10 / x
    except ValueError as exc:
        return "value:" + str(exc)
    except ZeroDivisionError:
        return "zero"


def custom_raise(kind):
    if kind == "key":
        raise KeyError("k")
    if kind == "value":
        raise ValueError("v")
    return "none"


def assertive(n):
    assert n >= 0, "n must be >= 0"
    return n * 2