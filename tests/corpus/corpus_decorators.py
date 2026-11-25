"""语料：装饰器 + property ✅。"""


def double(fn):
    """装饰器本身走**函数级回退** ✅（体内有嵌套 def ✅）。"""

    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs) * 2

    return wrapper


def tag(fn):
    def wrapper(*args, **kwargs):
        return "tag(" + str(fn(*args, **kwargs)) + ")"

    return wrapper


@double
def twice(value):
    return value


@tag
@double
def composed(value):
    return value + 1


class Box:
    def __init__(self, value):
        self.value = value

    @property
    def doubled(self):
        return self.value * 2

    @property
    def label(self):
        return "box:" + str(self.value)

    @staticmethod
    def helper(x):
        return x + 1

    @classmethod
    def build(cls, value):
        return cls(value)

    @double
    def scaled(self):
        return self.value

    def plain(self):
        return self.value


def box_ops():
    box = Box(5)
    built = Box.build(7)
    return (
        box.doubled,
        box.label,
        Box.helper(1),
        built.value,
        box.scaled(),
        box.plain(),
    )


def module_level():
    return twice(3), composed(2)


def class_decorator_marker(cls):
    cls.marked = True
    return cls


@class_decorator_marker
class Marked:
    def __init__(self, value):
        self.value = value


def marked_ops():
    return Marked.marked, Marked(1).value
