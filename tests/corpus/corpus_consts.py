"""语料：常量密集循环 ✅（常量池的意义 ✅）。"""


def loop_str(n):
    total = 0
    for i in range(n):
        text = "abc"
        total = total + len(text)
    return total


def loop_num(n):
    total = 0
    for i in range(n):
        total = total + 2 + 3
    return total


def loop_bytes(n):
    total = 0
    for i in range(n):
        data = b"ab"
        total = total + len(data)
    return total
