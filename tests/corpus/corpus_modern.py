"""语料：**海象运算符** ✅ / **del 属性与下标** ✅ / **hasattr 真值** ✅。"""


class Box:
    def __init__(self):
        self.value = 1
        self.data = {"k": 2}


class Tagged:
    def tag(self):
        return "tagged"


class Other:
    def tag(self):
        return "other"

    def extra(self):
        return "extra"


class Both(Tagged, Other):
    """**多基类** ✅（MRO 交给 CPython ✅）。"""

    def describe(self):
        return super().tag()


def walrus(values):
    total = 0
    while (piece := len(values)) > 0:
        total = total + piece
        values = values[1:]
    return total


def walrus_in_if(values):
    if (count := len(values)) > 2:
        return count * 10
    return count


def delete_targets():
    box = Box()
    del box.value
    del box.data["k"]
    return hasattr(box, "value"), hasattr(box, "data"), len(box.data)


def multi_base():
    item = Both()
    return item.tag(), item.extra(), item.describe()


def hasattr_truth():
    box = Box()
    return hasattr(box, "value"), hasattr(box, "missing")
