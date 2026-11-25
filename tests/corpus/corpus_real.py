"""**更接近真实业务的语料** ✅ —— 让基准不只看"纯算术循环" ✅。

这组用例的动机 ✅：`corpus_basic/consts` 里大多是**纯算术/纯属性**的热点 ✅，
而真实程序更多是 **字符串处理 ✅ + 字典计数 ✅ + 结构组装 ✅ + 小对象** ✅。
不把这几种放进来 ✅，GEO 就会被"我们最擅长的那几类"**高估** ✅。
"""


def wordfreq(text, top):
    """**词频统计** ✅ —— split/字典计数/排序切片 ✅（字符串 + 字典密集 ✅）。"""
    counts = {}
    for word in text.split():
        lowered = word.lower()
        counts[lowered] = counts.get(lowered, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return ranked[:top]


def matrix_ops(size):
    """**二维表统计** ✅ —— 嵌套循环 + 整数累加 + 列表索引 ✅（数值 + 容器混合 ✅）。"""
    table = []
    for row in range(size):
        line = []
        for col in range(size):
            line.append(row * col + col)
        table.append(line)
    total = 0
    best = 0
    for row in range(size):
        line = table[row]
        for col in range(size):
            value = line[col]
            total = total + value
            if value > best:
                best = value
    return total, best


def build_records(items):
    """**结构组装** ✅ —— 字典/列表/元组构造 ✅（真实代码里最常见的"搬运" ✅）。"""
    records = []
    for index, name in enumerate(items):
        record = {
            "id": index,
            "name": name,
            "length": len(name),
            "tags": [name[:2], name[-2:]],
        }
        records.append(record)
    return records


def tokenize(text):
    """**逐字符扫描** ✅ —— 字符串下标/切片/拼接 ✅（解析器的典型形状 ✅）。"""
    tokens = []
    current = ""
    for char in text:
        if char.isalnum():
            current = current + char
        else:
            if current:
                tokens.append(current)
                current = ""
    if current:
        tokens.append(current)
    return tokens
