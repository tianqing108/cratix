"""语料：**编译期健壮性** ✅ —— 单个定义不支持时只降级它 ✅，不能拖垮整个模块 ✅。"""


def gen_inside():
    """嵌套 def 里有 `yield` ✅ -> 那个 def 回退 ✅，外层函数照常 AOT ✅。"""

    def inner(n):
        for i in range(n):
            yield i

    return sum(inner(3))


class BrokenBody:
    """类体里有 `x: int`（无值注解）✅ -> 这个类降级成源码 exec ✅，模块照常 AOT ✅。"""

    limit: int = 7

    def value(self):
        return self.limit


def use_broken_body():
    return BrokenBody().value()


def plain(value):
    return value * 3
