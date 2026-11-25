"""语料：**星号解包** ✅（`a, *b, c = seq` ✅）与普通解包 ✅。"""


def head_rest(items):
    first, *rest = items
    return first, rest


def split_middle(items):
    first, *middle, last = items
    return first, middle, last


def no_suffix(items):
    head, *tail = items
    return head, tail


def starred_only(values):
    *all_items, = values
    return all_items


def swap(values):
    a, b = values
    b, a = a, b
    return a, b


def nested_unpack(pairs):
    (x, y), z = pairs
    return x + y + z
