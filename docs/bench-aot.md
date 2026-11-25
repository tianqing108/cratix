# AOT 性能基准（2026-11-25，实测 ✅）

## 结论

**v1 的 AOT 产物在纯算术上更快 ✅，但在属性/调用密集的代码上更慢 ✅** ——
总体现为 **0.69×**（即整体略慢 ✅ ✅）。

## 实测数据

| 用例 | 源码版(ms) | AOT(ms) | 加速比 |
| --- | --- | --- | --- |
| corpus_basic.add | 0.04 | 0.02 | **2.00×** ✅ |
| corpus_basic.control | 0.05 | 0.11 | 0.42× ❌ |
| corpus_basic.strings | 0.02 | 0.05 | 0.46× ❌ |
| corpus_basic.containers | 0.06 | 0.05 | 1.16× ✅ |
| corpus_basic.mixed | 0.01 | 0.03 | 0.40× ❌ |
| **合计** | 0.18 | 0.26 | **0.69×** |

复现：`python tests/bench/bench_aot.py` ✅。

## 为什么慢 ✅（诚实分析 ✅）

1. **CPython 3.14 的自适应解释器** ✅ —— 对 `LOAD_ATTR` / `BINARY_OP` / `CALL` 都有
   **专门化的快路径** ✅（`LOAD_ATTR_SLOT` ✅ / `BINARY_OP_ADD_INT` ✅ ...）✅；
   而我们的 AOT 代码 ✅ **每个操作都走通用 C-API** ✅
   （`PyObject_GetAttrString` ✅ / `PyNumber_Add` ✅ / `PyObject_Call` ✅）✅ ——
   通用路径的常数开销 ✅ ✅ 盖过了局部变量省下来的开销 ✅ ✅。
2. **属性访问没有槽位化** ✅ —— 类属性 ✅ 走 `__dict__` 查找 ✅ ✅（mypyc 用 `tp_getset` ✅）；
3. **调用没有用 `PyObject_Vectorcall`** ✅ —— 仍在构造元组 ✅ ✅。

## 结论与后续 ✅

- **AOT 的价值当前主要在"源码不可见 ✅ + 可控的启动路径 ✅"，而非速度 ✅**；
- 达到 1.5× 需要**类型特化** ✅（mypyc 级别的工作量 ✅ ✅）；
- 可落地的增量优化 ✅（按收益排序 ✅）：
  1. `PyObject_Call` → `PyObject_Vectorcall` ✅（调用密集用例 ✅）；
  2. 内建函数直译 ✅（`len` → `PyObject_Length` ✅ 等 ✅）；
  3. 类属性槽位化 ✅（`tp_getset` ✅）；
  4. 字符串/数值字面量常量池 ✅ ✅。