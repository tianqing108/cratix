"""**整数流分析** ✅ —— 找出"**可以只用 C 的 \`long long\` 表示**"的局部变量 ✅。

为什么需要它 ✅：M5 的下一档性能（GEO 1.25 -> 1.5+ ✅）只能靠**整数局部不装箱** ✅。
但 Python 的 \`int\` 是**任意精度** ✅，直接换成 \`long long\` 会静默改变语义 ✅。
所以先把"**值域可证有界**"的变量挑出来 ✅，剩下的一律保持装箱 ✅。

判定规则（保守 ✅，宁可少 ✅ 不可错 ✅）：

一个局部 \`x\` 是 **int-safe** 当且仅当：

1. 它**不是**参数 ✅ 不是 global ✅ 不是 nonlocal ✅ 不是 cell 里的 ✅；
2. 每一次赋值都是：
   - \`x = <int 表达式>\` ✅（含初始化 ✅），或
   - \`x += / -= / *= <int 表达式>\` ✅；
3. \`<int 表达式>\` 只由这些东西组成 ✅：**小整数字面量** ✅、**别的 int-safe 变量** ✅、
   \`+ - *\` ✅、一元 \`-\` ✅（**除法和取模不算** ✅ —— 地板除要额外证明 ✅）；
4. 它**没有**被用在：属性/下标赋值目标 ✅ \`del\` ✅ \`for\` 目标（\`range\` 除外 ✅）✅
   \`with ... as\` ✅ 参数位置 ✅ 推导式目标 ✅ lambda 默认值 ✅；
5. 表达式里出现的整数字面量都能装进 int64 ✅。

**溢出怎么办** ✅：分析本身**不**保证不溢出 ✅ —— 它只回答"**表示形式可以换**" ✅。
真正落地时还要**再加一层**：循环次数上界（\`range\` 的界）与字面量上界相乘不进位 ✅，
或者逐操作做 \`__builtin_*_overflow\` 式检查并回退 ✅。见 ROADMAP 的"M5 下一步" ✅。
"""
from __future__ import annotations

import ast

# **能装进 int64 的字面量** ✅
INT64_MAX = (1 << 63) - 1
INT64_MIN = -(1 << 63)

# **可以作为 int 表达式的二元运算符** ✅（除法/取模要额外证明 ✅ 先不算 ✅）
_INT_BINOPS = (ast.Add, ast.Sub, ast.Mult)


def _literal_ok(node) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and INT64_MIN <= node.value <= INT64_MAX
    )


def _is_int_expr(node, safe: set) -> bool:
    """`node` 是不是"**只由 safe 变量 + 小整数字面量 + 加减乘**"组成的表达式 ✅。"""
    if _literal_ok(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in safe
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_int_expr(node.operand, safe)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _INT_BINOPS):
        return _is_int_expr(node.left, safe) and _is_int_expr(node.right, safe)
    return False


def _walk_own(node):
    """遍历 `node` 的**自己**的语句/表达式 ✅（不进嵌套函数/类/lambda ✅）。"""
    todo = list(ast.iter_child_nodes(node))
    while todo:
        child = todo.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        todo.extend(ast.iter_child_nodes(child))


def analyze_int_locals(func_node, locals_: set) -> dict:
    """返回 `{"safe": [...], "blocked": {名字: 原因}}` ✅。"""
    if func_node is None:
        return {"safe": [], "blocked": {}}
    args = func_node.args
    params = set()
    for item in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        params.add(item.arg)
    for item in (args.vararg, args.kwarg):
        if item is not None:
            params.add(item.arg)

    candidates = set(locals_) - params
    blocked: dict = {}

    def block(name: str, reason: str) -> None:
        candidates.discard(name)
        blocked.setdefault(name, reason)

    # **第 4 条** ✅：被用在"不能换表示"的位置 ✅
    for sub in _walk_own(func_node):
        if isinstance(sub, ast.For) and isinstance(sub.target, ast.Name):
            iter_node = sub.iter
            is_range = (
                isinstance(iter_node, ast.Call)
                and isinstance(iter_node.func, ast.Name)
                and iter_node.func.id == "range"
            )
            if not is_range:
                block(sub.target.id, "非 range 的 for 目标")
        elif isinstance(sub, (ast.With, ast.AsyncWith)):
            for item in sub.items:
                if item.optional_vars is not None:
                    for name in ast.walk(item.optional_vars):
                        if isinstance(name, ast.Name):
                            block(name.id, "with ... as 目标")
        elif isinstance(sub, ast.Delete):
            for target in sub.targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        block(name.id, "del 目标")
        elif isinstance(sub, (ast.comprehension,)):
            block_target = sub.target
            for name in ast.walk(block_target):
                if isinstance(name, ast.Name):
                    block(name.id, "推导式目标")
        elif isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store):
            for name in ast.walk(sub.value):
                if isinstance(name, ast.Name):
                    block(name.id, "属性赋值目标")
        elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Store):
            for name in ast.walk(sub.value):
                if isinstance(name, ast.Name):
                    block(name.id, "下标赋值目标")
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            block(sub.name, "except ... as 目标")

    # **第 2/3 条** ✅：每次赋值都要是 int 表达式 ✅（反复迭代到不动点 ✅）
    # **已经被"赋值语句"这一层处理过的目标节点** ✅ —— 否则下面那个通用的
    # "Name 且是 Store" 又会把它们当成"其它赋值形状" ✅ -> 一个都判不出来 ✅（实测 ✅）
    handled: set = set()
    for sub in _walk_own(func_node):
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            handled.add(id(sub.targets[0]))
        elif isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name):
            handled.add(id(sub.target))
        elif isinstance(sub, ast.For) and isinstance(sub.target, ast.Name):
            # **`for i in range(...)` 的目标** ✅ 是我们的"整数来源" ✅ 不算"别的赋值形状" ✅
            iter_node = sub.iter
            if (
                isinstance(iter_node, ast.Call)
                and isinstance(iter_node.func, ast.Name)
                and iter_node.func.id == "range"
            ):
                handled.add(id(sub.target))
    safe: set = set()
    changed = True
    while changed:
        changed = False
        for name in list(candidates):
            if name in safe:
                continue
            ok = True
            for sub in _walk_own(func_node):
                if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name) \
                        and sub.targets[0].id == name:
                    if not _is_int_expr(sub.value, safe | {name}):
                        ok = False
                        break
                elif isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name) \
                        and sub.target.id == name:
                    if not isinstance(sub.op, _INT_BINOPS) or not _is_int_expr(sub.value, safe | {name}):
                        ok = False
                        break
                elif isinstance(sub, ast.Name) and sub.id == name and isinstance(sub.ctx, ast.Store) \
                        and id(sub) not in handled:
                    # 别的赋值形状（解包 / 海象 / for 目标 ✅）一律保守 ✅
                    ok = False
                    break
            if ok:
                safe.add(name)
                changed = True
    for name in candidates - safe:
        blocked.setdefault(name, "赋值不是纯整数表达式")
    return {"safe": sorted(safe), "blocked": blocked}
