"""语料：**元数据保真** ✅（docstring / 签名 / qualname / module ✅）。"""


def documented(a, b=2, *rest, key=None, **kw):
    """文档：加法 ✅。"""
    return a + b


def undocumented(x):
    return x


def defaults_only(a=1, b="two", c=None, d=True):
    """只有默认值 ✅。"""
    return a


class Widget:
    """控件的文档 ✅。"""

    kind = "widget"

    def __init__(self, size, label="w"):
        """构造 ✅。"""
        self.size = size
        self.label = label

    def area(self, scale=1):
        """面积 ✅。"""
        return self.size * scale

    def plain(self):
        return self.size
