"""语料：**裸 raise** ✅（重抛当前异常 ✅）+ **类体里的类** ✅。"""


class Outer:
    """类体里的类 ✅（源码 exec 后挂到外层类上 ✅）。"""

    class Inner:
        def hello(self):
            return "inner"

    def make(self):
        return Outer.Inner()


def reraise(value):
    try:
        if value < 0:
            raise ValueError("negative")
        return "ok"
    except ValueError:
        raise


def catch_outer(value):
    try:
        return reraise(value)
    except ValueError as exc:
        return "caught:" + str(exc)


def nested_reraise(value):
    try:
        try:
            raise KeyError(value)
        except KeyError:
            raise
    except KeyError as exc:
        return "k:" + str(exc.args[0])


def nested_class_use():
    return Outer().make().hello()
