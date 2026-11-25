"""语料：**同一个属性名出现在多条互斥分支里** ✅。

**背景** ✅（pitfall #133 ✅）：interned 属性名的初始化以前只落在**首次使用的那条分支**里 ✅ ->
别的分支用到同一个属性名时 static 还是 NULL ✅ -> `PyObject_GetAttr(obj, NULL)` ✅ -> 段错误 ✅。
"""


class Box:
    def __init__(self):
        self.code = 7
        self.label = "box"


def read_in_second_branch(flag):
    """属性名**第一次**出现在 `if` 的第一条分支 ✅，但实际执行的是第二条 ✅。"""
    box = Box()
    if flag:
        return box.label
    return str(box.code)


def raise_in_second_handler(value):
    """两个 `except` 都读 `exc.code` ✅ —— 只有第二个会真的执行 ✅。"""

    class Boom(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code

    if value > 0:
        try:
            raise Boom("pos", "正数")
        except Boom as exc:
            return "1:" + exc.code
    try:
        raise Boom("neg", "负数")
    except Boom as exc:
        return "2:" + exc.code + "/" + str(exc)
    return "end"


def two_attr_branches(flag):
    box = Box()
    if flag:
        first = box.label
        return first + str(box.code)
    second = box.code
    return str(second)
