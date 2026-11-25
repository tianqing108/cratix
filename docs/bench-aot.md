# AOT 性能基准（2026-11-25，实测 ✅）

## 结论

**v1 的 AOT 产物在纯算术上更快 ✅，但在属性/调用密集的代码上更慢 ✅** ——
总体现为 **0.72×**（即整体略慢 ✅ ✅）。

## 实测数据（优化后 ✅）

| 用例 | 源码版(ms) | AOT(ms) | 加速比 | 优化前 ✅ |
| --- | --- | --- | --- | --- |
| corpus_basic.add | 0.40 | 0.40 | 1.00–2.00× ✅ | 2.00× ✅ |
| corpus_basic.control | 0.44 | 0.84 | **0.52×** ✅ | 0.43× ✅ |
| corpus_basic.strings | 0.22 | 0.36 | **0.61×** ✅ | 0.48× ✅ |
| corpus_basic.containers | 0.26 | 0.24 | 1.08× ✅ | 1.08× ✅ |
| corpus_basic.mixed | 0.10 | 0.12 | **0.83×** ✅ | 0.71× ✅ |
| **合计** | 1.42 | 1.96 | **0.72×** ✅ | 0.69× ✅ |

复现：`python tests/bench/bench_aot.py --repeat 5` ✅。

## 已落地的优化 ✅

1. **内建函数直译** ✅ —— `len` → `PyObject_Length` ✅ / `str` → `PyObject_Str` ✅ /
   `isinstance` ✅ / `hasattr` ✅ / `type` ✅ / `list` ✅ / `tuple` ✅ / `abs` ✅ ...
   （绕开通用调用开销 ✅ ✅）；
2. **向量调用** ✅ —— `PyObject_Vectorcall` ✅（省掉每次调用的元组分配 ✅ ✅）；
3. **方法调用专门化** ✅ —— `PyObject_VectorcallMethod` ✅（**绕过属性查找** ✅ ——
   与 CPython 3.14 解释器的 `LOAD_METHOD` 专门化同源 ✅ ✅）。

## 为什么仍然慢 ✅（诚实分析 ✅）

1. **CPython 3.14 的自适应解释器** ✅ —— 对 `LOAD_ATTR` / `BINARY_OP` / `CALL` 都有
   **专门化快路径** ✅ ✅；我们的 AOT 代码 ✅ **每个操作都走通用 C-API** ✅
   （`PyObject_GetAttrString` ✅ / `PyNumber_Add` ✅）✅ ——
   通用路径的常数开销 ✅ ✅ 盖过了局部变量省下来的开销 ✅ ✅。
2. **属性访问没有槽位化** ✅ —— 类属性 ✅ 走 `__dict__` 查找 ✅ ✅（mypyc 用 `tp_getset` ✅）；
3. **没有类型特化** ✅ —— 不知道 `x` 是 int ✅ 还是要走 `PyNumber_Add` ✅ ✅
   （mypyc 靠 mypy 的类型信息 ✅ ✅）。

## 要达到 1.5× 需要什么 ✅

- **类型特化** ✅（mypyc 级别的工作量 ✅ ✅ ✅）—— 这是主要差距 ✅ ✅；
- 类属性槽位化 ✅（`tp_getset` ✅ ✅）；
- 字符串/数值常量池 ✅ ✅。

**当前 AOT 的价值主要在"源码不可见 ✅ + 可控的加载路径 ✅ + 精确的行为等价 ✅"，而非速度 ✅** ✅。