# AOT 覆盖率报告 ✅（自动生成 ✅）

> 由 `scripts/coverage_report.py` 生成 ✅ —— 扫的是**本机标准库** ✅，不是抽样 ✅。
> 时间 ✅：2026-11-26 06:37
> Python ✅：3.14.7（cpython-314）

## 总览

| 指标 | 数值 |
| --- | --- |
| 扫描模块 | 642 |
| **编译成功模块** | **633** |
| 模块级回退 | 9 |
| **崩溃** | **0** |
| **函数 AOT** | **14173** |
| 函数回退 | 419 |
| AOT 比例 | **0.971** |

## 函数级回退原因（top 12）

| 次数 | 原因 |
| --- | --- |
| 184 | 生成器 |
| 132 | async 方法 |
| 28 | yield |
| 16 | 类定义带关键字参数 |
| 13 | 嵌套定义 |
| 12 | 类属性赋值目标不是简单名字 |
| 6 | 表达式类型：YieldFrom |
| 5 | 类方法回退：__iter__ |
| 5 | async 函数 |
| 4 | match 模式：MatchMapping |
| 3 | match 模式：MatchClass |
| 3 | try 的 handler 过多 |

## 模块级回退原因（top 12）

| 次数 | 原因 |
| --- | --- |
| 3 | yield |
| 2 | 模块级语句不支持：TypeAlias |
| 2 | 表达式类型：TemplateStr |
| 1 | 语句类型：AsyncFunctionDef |
| 1 | 表达式类型：YieldFrom |

## 回退最多的模块（top 15）

| 回退函数数 | 模块 |
| --- | --- |
| 30 | asyncio/events.py |
| 29 | asyncio/base_events.py |
| 26 | xml/etree/ElementPath.py |
| 15 | difflib.py |
| 15 | asyncio/locks.py |
| 14 | contextlib.py |
| 12 | traceback.py |
| 11 | pdb.py |
| 10 | glob.py |
| 10 | asyncio/selector_events.py |
| 10 | asyncio/proactor_events.py |
| 8 | ipaddress.py |
| 7 | xml/etree/ElementTree.py |
| 7 | weakref.py |
| 7 | calendar.py |

## 这些回退意味着什么 ✅

- **回退 ≠ 错误** ✅：回退的函数**照常能跑** ✅（用源码 exec ✅，速度与 .pyc 相当 ✅），
  只是没有享受到 AOT 的收益 ✅；
- **生成器 ✅ / async ✅** 是最大的两块 ✅ —— 它们需要**协程/生成器状态机** ✅，
  属于另一个量级的工程 ✅；
- **模块级回退**很小 ✅ —— 一个不支持的字面量/语句会拖垮整个模块 ✅，
  所以这块一直在按「原因排行榜」清理 ✅。
