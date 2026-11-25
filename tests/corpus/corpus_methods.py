"""语料：**类方法调用** ✅（直调优化 ✅ / 属性覆盖时不许直调 ✅）。"""


class Machine:
    def __init__(self, value):
        self.value = value

    def step(self, amount):
        return self.value + amount

    def run(self, n):
        total = 0
        for i in range(n):
            total = total + self.step(i)
        return total

    def twice(self):
        return self.value * 2


class Shadowed:
    """`self.step` 被**实例属性**覆盖过 ✅ —— 必须走普通属性查找 ✅（不许直调 ✅）。"""

    def __init__(self, value):
        self.value = value
        self.step = self.other

    def other(self, amount):
        return self.value - amount

    def run(self, n):
        total = 0
        for i in range(n):
            total = total + self.step(i)
        return total


def self_calls(n):
    return Machine(1).run(n)


def shadow_calls(n):
    return Shadowed(10).run(n)


def attribute_reads(n):
    machine = Machine(2)
    total = 0
    for i in range(n):
        total = total + machine.value + machine.twice()
    return total
