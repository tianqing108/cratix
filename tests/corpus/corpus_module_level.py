"""语料：**模块级语句** ✅（if/try/expr/del ✅）+ **类级回退** ✅（继承的类走源码 exec ✅）。"""

MODE = "dev"

try:
    from os.path import basename as _basename
except ImportError:  # pragma: no cover
    _basename = None

if MODE == "dev":
    LIMIT = 10
else:
    LIMIT = 100

REGISTRY = {}
REGISTRY["limit"] = LIMIT


class AppError(Exception):
    """**继承内置类** ✅ -> 这个类走**类级回退** ✅（源码 exec ✅），模块别的函数照常 AOT ✅。"""

    def __init__(self, code):
        super().__init__("app error " + str(code))
        self.code = code


def limit_value():
    return LIMIT


def registry_size():
    return len(REGISTRY)


def raise_and_catch(code):
    try:
        raise AppError(code)
    except AppError as exc:
        return exc.code


def basename_of(path):
    return _basename(path)


_TEMP = "gone"


def drop_temp():
    global _TEMP
    _TEMP = None
    del _TEMP
    return "dropped"
