"""语料：try/except ✅。"""


def safe_div(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None


def safe_div2(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None
    except TypeError:
        return "type"


def nested(a):
    try:
        try:
            return 10 / a
        except ZeroDivisionError:
            return "inner"
    except TypeError:
        return "outer"