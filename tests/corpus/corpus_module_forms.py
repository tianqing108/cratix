"""语料：**`from m import *`** ✅ + **模块体控制流里的类** ✅。"""

from math import *


MODE = "fast"

if MODE == "fast":
    class Impl:
        def run(self):
            return "fast"
else:
    class Impl:
        def run(self):
            return "slow"


def root_and_floor(value):
    return sqrt(value) + floor(2.7)


def use_impl():
    return Impl().run()


def builtin_from_star():
    return abs(-3) + pow(2, 3)
