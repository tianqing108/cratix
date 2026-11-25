"""示例应用：命令行笔记 ✅（刻意用上 AOT 支持的多种特性 ✅）。"""

import json
import sys
from pathlib import Path

CATEGORIES = ("work", "life", "idea")


class NoteError(Exception):
    """业务异常 ✅（继承内置类 ✅）。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Note:
    def __init__(self, text, category):
        self.text = text
        self.category = category
        self.done = False

    def describe(self):
        mark = "x" if self.done else " "
        return f"[{mark}] {self.text} ({self.category})"

    def to_dict(self):
        return {"text": self.text, "category": self.category, "done": self.done}

    @classmethod
    def from_dict(cls, payload):
        item = cls(payload["text"], payload["category"])
        item.done = bool(payload["done"])
        return item


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.items = []
        self._load()

    def _load(self):
        if not self.path.is_file():
            return
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return
        payload = json.loads(raw)
        self.items = [Note.from_dict(entry) for entry in payload]

    def save(self):
        payload = [item.to_dict() for item in self.items]
        # **`Path.write_text`** ✅ —— #133（属性名静态量）修好之后 ✅ 这条路径不再崩 ✅
        # （以前崩是因为 `encoding` 那个属性名的初始化只落在别的分支里 ✅）
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def add(self, text, category):
        if category not in CATEGORIES:
            raise NoteError("bad-category", "未知分类：" + category)
        if not text.strip():
            raise NoteError("empty-text", "内容不能为空")
        self.items.append(Note(text, category))
        self.save()
        return len(self.items)

    def finish(self, index):
        if index < 0 or index >= len(self.items):
            raise NoteError("bad-index", "序号越界：" + str(index))
        self.items[index].done = True
        self.save()

    def pending(self):
        return [item for item in self.items if not item.done]

    def stats(self):
        total = len(self.items)
        done = sum(1 for item in self.items if item.done)
        by_category = {}
        for item in self.items:
            by_category[item.category] = by_category.get(item.category, 0) + 1
        parts = [f"{name}={count}" for name, count in sorted(by_category.items())]
        return f"总 {total} ✅ / 完成 {done} ✅ / " + " ".join(parts)


class Options:
    """解析后的命令行选项 ✅（手写解析 ✅ —— 不用 argparse ✅）。"""

    def __init__(self):
        self.store = "notes.json"
        self.command = ""
        self.text = ""
        self.category = "life"
        self.index = 0


def parse_args(argv):
    """**手写解析** ✅：`--store P` ✅ / `add TEXT [--category C]` ✅ / `done N` ✅ / `list` ✅ / `stats` ✅。"""
    options = Options()
    rest = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--store" and index + 1 < len(argv):
            options.store = argv[index + 1]
            index = index + 2
            continue
        if item == "--category" and index + 1 < len(argv):
            options.category = argv[index + 1]
            index = index + 2
            continue
        rest.append(item)
        index = index + 1
    if not rest:
        raise NoteError("no-command", "缺少命令")
    options.command = rest[0]
    if options.command == "add":
        if len(rest) < 2:
            raise NoteError("no-text", "add 需要内容")
        options.text = rest[1]
    if options.command == "done":
        if len(rest) < 2:
            raise NoteError("no-index", "done 需要序号")
        options.index = int(rest[1])
    return options


def run(argv):
    try:
        args = parse_args(argv)
    except NoteError as exc:
        return "出错了 ✅（" + exc.code + "）：" + str(exc)
    store = Store(args.store)
    try:
        if args.command == "add":
            count = store.add(args.text, args.category)
            return "已添加 ✅（共 " + str(count) + " 条）"
        if args.command == "done":
            store.finish(args.index)
            return "已标记完成 ✅"
        if args.command == "list":
            pending = store.pending()
            if not pending:
                return "（没有未完成的）"
            return chr(10).join(item.describe() for item in pending)
        if args.command == "stats":
            return store.stats()
    except NoteError as exc:
        return "出错了 ✅（" + exc.code + "）：" + str(exc)
    return "未知命令：" + args.command


def main():
    text = run(sys.argv[1:])
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
