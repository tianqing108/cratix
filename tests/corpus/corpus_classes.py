"""语料：类 ✅。"""


class Point:
    """二维点 ✅。"""

    kind = "point"

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def norm2(self):
        return self.x * self.x + self.y * self.y

    def shifted(self, dx, dy=0):
        return Point(self.x + dx, self.y + dy)

    def describe(self):
        return self.kind + ":" + str(self.x) + "," + str(self.y)