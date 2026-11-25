"""语料：**函数体里的类** ✅（不捕获外层局部的那种 ✅）+ 编译器不崩 ✅。"""


def local_class_only():
    class Point:
        def __init__(self):
            self.x = 3

        def value(self):
            return self.x

    return Point().value()


def class_from_module_globals():
    class Helper:
        def doubled(self, value):
            return value * FACTOR

    return Helper().doubled(4)


FACTOR = 5


def capturing_class_falls_back():
    """**类里的方法捕获外层局部** ✅ -> 这个函数回退 ✅（语义正确 ✅）。"""
    step = 2

    class Counter:
        def next(self, value):
            return value + step

    return Counter().next(1)
