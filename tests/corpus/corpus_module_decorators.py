"""语料：**模块级装饰器** ✅（跳板 + 套装饰器 + 换回公开名 ✅）。"""

from functools import lru_cache


@lru_cache(maxsize=None)
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def doubled(fn):
    def wrapper(value):
        return fn(value) * 2

    return wrapper


def tagged(fn):
    def wrapper(value):
        return "[" + str(fn(value)) + "]"

    return wrapper


@doubled
def plus_one(value):
    return value + 1


@tagged
@doubled
def stacked(value):
    return value * 3


def cache_stats():
    before = fib.cache_info()
    fib(12)
    after = fib.cache_info()
    return after.hits > before.hits


def calls():
    return plus_one(4), stacked(2), fib(10)
