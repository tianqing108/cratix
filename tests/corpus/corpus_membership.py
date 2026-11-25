"""语料：**`in` / `not in`** ✅ —— 极性写反过一次（恒为 False ✅）。"""

CATEGORIES = ("work", "life", "idea")
NUMBERS = [1, 2, 3]
TEXT = "hello world"


def has_category(name):
    return name in CATEGORIES


def lacks_category(name):
    return name not in CATEGORIES


def branch_on_membership(name):
    if name not in CATEGORIES:
        return "missing"
    return "ok"


def has_number(value):
    return value in NUMBERS


def has_substring(piece):
    return piece in TEXT


def chained(value):
    """**链式比较里含 `in`** ✅。"""
    return 0 < value < 3 and value in NUMBERS
