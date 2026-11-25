"""语料：**继承内置类 / 自定义基类** ✅（非槽位布局 ✅ + 零参 `super()` 改写 ✅）。"""


class AppError(Exception):
    """继承内置异常 ✅ —— 实例布局是 `Exception` 的 ✅（我们**不做槽位** ✅）。"""

    def __init__(self, code):
        super().__init__("bad " + str(code))
        self.code = code

    def describe(self):
        return "E" + str(self.code)


class Base:
    def __init__(self, value):
        self.value = value

    def doubled(self):
        return self.value * 2

    def label(self):
        return "base"


class Child(Base):
    def __init__(self, value):
        super().__init__(value)
        self.extra = 1

    def total(self):
        return self.doubled() + self.extra

    def label(self):
        return super().label() + "-child"


def raise_app(code):
    try:
        raise AppError(code)
    except AppError as exc:
        return exc.describe()


def child_total(value):
    return Child(value).total()


def child_label(value):
    return Child(value).label()


def is_exception():
    return issubclass(AppError, Exception)
