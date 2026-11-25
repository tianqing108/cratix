"""语料：**match 语句** ✅（字面量/单例/捕获/通配/或 + guard ✅）。"""


def classify(value):
    match value:
        case 0:
            return "zero"
        case 1 | 2:
            return "small"
        case None:
            return "none"
        case other:
            return "other:" + str(other)


def guard_case(value):
    match value:
        case 0:
            return "zero"
        case n if n > 10:
            return "big"
        case _:
            return "small"


def with_block(value):
    match value:
        case 0:
            return 0
        case n:
            total = n * 2
            if total > 10:
                return 99
            return total


def nested_match(value, flag):
    match value:
        case 0:
            match flag:
                case True:
                    return "zero-true"
                case _:
                    return "zero-false"
        case _:
            return "other"
