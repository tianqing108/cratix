"""语料：**语句组合** ✅ + **类特性** ✅ —— 差分测试用（源码版当基准 ✅）。"""

STATE = 0


class Base:
    def __init__(self, value):
        self.value = value

    def describe(self):
        return "base:" + str(self.value)

    def __repr__(self):
        return "Base(" + str(self.value) + ")"


class Derived(Base):
    def __init__(self, value, extra):
        super().__init__(value)
        self.extra = extra

    def describe(self):
        return super().describe() + "+" + str(self.extra)

    def total(self):
        return self.value + self.extra

    @classmethod
    def build(cls, value):
        return cls(value, value * 2)

    @staticmethod
    def helper(value):
        return value + 100


class Comparable:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, Comparable) and self.value == other.value

    def __repr__(self):
        return "Comparable(" + str(self.value) + ")"


def for_else(values):
    total = 0
    for item in values:
        if item > 5:
            total = -1
            break
        total += item
    else:
        total += 1000
    return total


def while_else(values):
    count = 0
    total = 0
    while count < len(values):
        if values[count] < 0:
            total = -1
            break
        total += values[count]
        count += 1
    else:
        total += 500
    return total


def nested_try(values):
    log = []
    try:
        try:
            log.append("inner")
            if values and values[0] == 1:
                raise ValueError("inner-boom")
            log.append("inner-ok")
        except ValueError:
            log.append("inner-caught")
            raise
        finally:
            log.append("inner-finally")
    except ValueError as exc:
        log.append("outer:" + str(exc))
    finally:
        log.append("outer-finally")
    return log


def try_in_loop(values):
    total = 0
    caught = 0
    for item in values:
        try:
            if item < 0:
                raise ValueError("neg")
            total += item
        except ValueError:
            caught += 1
            continue
        finally:
            total += 1
    return total, caught


def finally_overrides(values):
    try:
        return "from-try"
    finally:
        values.append("finally-ran")


def multiple_with(left, right):
    class Guard:
        def __init__(self, name, log):
            self.name = name
            self.log = log

        def __enter__(self):
            self.log.append("enter:" + self.name)
            return self.name

        def __exit__(self, exc_type, exc, tb):
            self.log.append("exit:" + self.name)
            return False

    log = []
    with Guard("a", log) as first, Guard("b", log) as second:
        log.append("body:" + first + second)
    return log


def chained_calls(text):
    words = text.split()
    lowered = [word.lower() for word in words]
    cleaned = "-".join(lowered).strip("-")
    return cleaned.upper(), cleaned.replace("-", "_"), cleaned.split("-")


def assert_ok(values):
    assert isinstance(values, list), "必须是列表"
    assert len(values) >= 0
    return len(values)


def global_write(delta):
    global STATE
    STATE += delta
    return STATE


def class_features():
    derived = Derived.build(3)
    return [
        derived.describe(),
        derived.total(),
        Derived.helper(1),
        repr(derived),
        isinstance(derived, Base),
        Derived(1, 2) == Derived(1, 2),
        repr(Comparable(7)),
        Comparable(7) == Comparable(7),
        Comparable(7) == Comparable(8),
    ]


def dict_of_lists(keys, values):
    grouped = {}
    for index, key in enumerate(keys):
        grouped.setdefault(key, []).append(values[index])
    return sorted((key, sorted(items)) for key, items in grouped.items())


def nested_mutation():
    matrix = [[0] * 2 for _ in range(2)]
    for row in range(2):
        for column in range(2):
            matrix[row][column] = row * 2 + column
    return matrix


def truthiness(values):
    checks = []
    for item in values:
        if item:
            checks.append(1)
        else:
            checks.append(0)
    return checks
