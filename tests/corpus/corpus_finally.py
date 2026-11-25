"""语料：try/else + try/finally ✅。"""

LOG = []


def _mark(tag):
    LOG.append(tag)
    return tag


def simple():
    LOG.clear()
    try:
        _mark("body")
    finally:
        _mark("finally")
    return "|".join(LOG)


def with_exception():
    LOG.clear()
    try:
        try:
            _mark("body")
            raise ValueError("boom")
        finally:
            _mark("finally")
    except ValueError:
        _mark("caught")
    return "|".join(LOG)


def with_return():
    LOG.clear()

    def inner():
        try:
            _mark("body")
            return "ret"
        finally:
            _mark("finally")

    return inner(), "|".join(LOG)


def with_break():
    LOG.clear()
    for i in range(3):
        try:
            _mark("iter" + str(i))
            if i == 1:
                break
        finally:
            _mark("fin" + str(i))
    return "|".join(LOG)


def with_continue():
    LOG.clear()
    for i in range(3):
        try:
            if i == 1:
                continue
            _mark("iter" + str(i))
        finally:
            _mark("fin" + str(i))
    return "|".join(LOG)


def else_runs():
    LOG.clear()
    try:
        _mark("body")
    except ValueError:
        _mark("caught")
    else:
        _mark("else")
    return "|".join(LOG)


def else_skipped():
    LOG.clear()
    try:
        _mark("body")
        raise ValueError("x")
    except ValueError:
        _mark("caught")
    else:
        _mark("else")
    return "|".join(LOG)


def nested():
    LOG.clear()
    try:
        try:
            _mark("inner")
            raise KeyError("k")
        finally:
            _mark("fin-inner")
    except KeyError:
        _mark("caught")
    finally:
        _mark("fin-outer")
    return "|".join(LOG)


def value_after():
    total = 0
    for i in range(4):
        try:
            if i % 2 == 0:
                continue
            total = total + i
        finally:
            total = total + 10
    return total
