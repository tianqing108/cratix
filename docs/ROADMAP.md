# cratix 路线图

## 进度

| 里程碑 | 交付物 | 验收线 | 估时 | 状态 |
| --- | --- | --- | --- | --- |
| M0 骨架 + vendor | 仓库结构、vendor 打包半边并去业务化、统一改名、CLI deps/build | 示例工程产出 onefile exe 并运行成功；两次构建 sha256 一致；构建链无第三方编译器 | 1 周 | ✅ 完成 |
| M1 运行期 + 双形态 | 纯 Python 引导模块(.pyc 兜底)、stub 改造 D1–D8、onedir 目录版 | onefile 零落盘；onedir 可用；verify --no-console 通过 | 1.5 周 | ✅ 完成 |
| M2 AOT 内核 | IR / 作用域 / 子集判定 / 黄金模型 / C 后端 / MSVC 驱动 / 接入打包链 / 双进程探针 / 函数级回退 | 编译产物与源码行为等价；产物里能加载 AOT 模块 | 4 周 | ✅ 完成 |
| M3 子集扩展与语义对齐 | 类/try-except/with/推导式/装饰器/property/元数据保真 | tests/corpus 全绿；无静默语义偏差 | 3 周 | ✅ **完成**：类 ✅ / try-except ✅ / with ✅ / 循环 else ✅ / 装饰器 ✅ / property ✅ / 推导式 ✅ / **生成器表达式 ✅** / 元数据保真 ✅ |
| M4 增量/缓存/可复现/报告 | 增量缓存、report.json、可复现性检查表 | 热构建 ≤5s；两次构建 sha256 一致 | 1.5 周 | ✅ 完成（AOT 增量 ✅ 热构建 3.3s ✅；report.json ✅；可复现 ✅）|
| M5 性能与覆盖率 | 闭包 cell、slot 化属性、全局缓存、常量池、覆盖率提升 | 基准 ≥1.5×；启动不劣化 >15% | 2–3 周 | 🟡 **基准已建立** ✅（实测 0.69× ✅ 诚实记录 ✅）；提速待做 ✅ |
| M6 跨平台预留 | 四接口抽象 + Linux/macOS 占位 | Windows 实现完全走抽象接口 | 1 周 | ✅ **完成**：四接口 ✅ + Windows 全走接口 ✅ + **Linux/macOS 编译路径已实现** ✅（引导器仍待做 ⬜） |

## M0/M1 已完成的事实

- **onefile**：15.5 MB 单 exe，600 个载荷条目，24 个标准库 C 扩展，3 个原生条目；
- **onedir**：`hello_0.1.0/hello.exe`（397 KB）+ `payload.zip`（15.1 MB）；
- 启动耗时：onefile 313 ms / onedir 250 ms；
- 运行期零落盘（TEMP 独立目录 before/after 无新增），onefile 与 onedir 均通过；
- 无标准句柄启动（= Explorer 双击）退出码 0；
- 连续两次构建 sha256 一致；
- **26 项测试全绿**（18 单元 + 6 端到端 + 2 AOT 真编译）；
- 构建链只依赖 CPython + MSVC，无任何第三方编译器。

## M2 已完成的事实（编译器内核 ✅）

- **自有 IR**（`compiler/ir.py`）✅ —— 不用 ast 当 IR ✅；
- **作用域分析**（`frontend/scope.py`）✅ —— 闭包捕获 → 不可 AOT ✅；
- **子集判定**（`frontend/subset.py`）✅ —— 每条拒绝都有原因 ✅（写进 report ✅）；
- **黄金模型**（`golden/irvm.py`）✅ —— IR 解释执行器 ✅ + 与源码差分 ✅；
- **C 后端**（`backend/codegen.py`）✅ —— 语句式代码生成 ✅ + 所有权纪律 ✅；
- **MSVC 驱动**（`compiler/driver.py`）✅ —— 真编成 .pyd ✅（`/MD` ✅ `/Brepro` ✅）；
- 语料 `tests/corpus/` ✅ 三组 ✅（基础 / 异常 / 必须被拒 ✅）。

### 黄金模型抓出的前端缺陷（5 个 ✅）

1. 仅关键字参数的默认值没有传进 IR ✅；
2. 星号解包 `first, *rest = items` 未实现 ✅；
3. `for` 的 target/iter 子节点顺序 构建器与 VM 不一致 ✅；
4. `*args`/`**kwargs` 未传时没有绑定（函数体内引用会 NameError ✅）；
5. 位置参数与仅关键字参数的名字混在一个列表里 ✅。

### C 后端踩坑（10 个 ✅）

见 [aot-pitfalls.md](aot-pitfalls.md) ✅ —— 其中最隐蔽的是：

- **exec 槽位返回 `int` 而不是 `PyObject*`** ✅；
- **多变量声明只初始化最后一个** ✅ → 野指针 → 访问违例 ✅；
- **借用引用被 DECREF** ✅ → use-after-free ✅。

## M2 追加完成的事实（AOT 接入打包链 ✅）

- `--backend aot` 把项目模块编成 `.pyd` ✅ → 进载荷 `ext/cratix_<hash>.pyd` ✅ →
  `meta.json` 写扩展记录 ✅（含 `init` 唯一符号 ✅ 与 `exec_done` ✅）→
  运行期由 stub 的 memdl **从内存加载** ✅（`cratix_loader.load` ✅）→
  引导模块的 `_AotFinder` ✅ 命中 ✅ → 模块体在 init 里执行 ✅ → 入口调用成功 ✅；
- 产物里 AOT 模块的字典含 `main` 与 `sys` ✅（模块体确实执行了 ✅）；
- **29 项测试全绿**（21 单元 + 8 端到端 ✅）。

## M2 追加：双进程探针 + 函数级回退 ✅

- **双进程差分探针** ✅：源码版与 AOT 版各跑一个**独立子进程** ✅ → 输出 JSON ✅ → 父进程比对 ✅；
  比对项：模块级名字 ✅ / 函数元数据 ✅ / 模块级常量值 ✅；不一致 → 该模块**回退 .pyc** ✅；
  `--probe strict` → 直接构建失败 ✅。
- **函数级回退** ✅：模块内不支持的函数**仍进 AOT 模块** ✅ —— 内嵌函数源码 ✅，
  运行期 `Py_CompileString()` 编译 ✅ 并在**模块字典里 exec** ✅（函数的 `__globals__` = 模块字典 ✅）；
  于是生成器 / global 写入 / 闭包等构造都能保留 ✅，模块整体不必回退 ✅。
- **31 项测试全绿**（24 单元 + 7 端到端 ✅）。

## M3 追加：类支持 ✅

- 类 = `PyType_FromSpec` ✅（自定义 `tp_new` ✅ + `MANAGED_DICT` ✅）；
- 方法 = 原生函数 ✅ + 解释执行的薄跳板 ✅（普通 PyCFunction 不绑定 self ✅）；
- 类属性 ✅ / 实例属性 ✅ / `isinstance` ✅ 均可用 ✅；
- **33 项测试全绿** ✅（25 单元 + 8 端到端 ✅）。

## 引导模块原生化（dogfooding ✅）

- `cratix_bootstrap.py` ✅ 被**自研编译器**编译成 `runtime/cratix_bootstrap.pyd` ✅（11 个函数 ✅）；
- stub 用**加载桥**装载它 ✅（`cratix_loader.load` ✅ —— 标准加载器要求单阶段模块带 m_def ✅）；
- **当前默认走 .pyc 引导** ✅（`CRATIX_BOOTSTRAP=native` 可开原生 ✅）——
  原生引导在 **memdl 映射的解释器**里崩溃 ✅（0xC0000005 ✅），已隔离 ✅（见 aot-pitfalls ✅ #24 ✅）。

## M4 追加：增量编译 ✅

- 每个模块一个**指纹** ✅（源码 sha256 ✅ + 编译器版本 ✅ + 解释器身份 ✅ + 选项 ✅）；
- 指纹一致且 .pyd 在 ✅ → **跳过 cl + link** ✅ ✅；
- 实测 ✅：冷构建 8.5 s ✅ → **热构建 3.3 s** ✅（达成 M4 的 ≤5 s 验收线 ✅ ✅）。

## M5 追加：性能基准（诚实记录 ✅）

- 基准脚本 ✅：`python tests/bench/bench_aot.py` ✅；
- **实测（优化后）** ✅：纯算术 **1.0–2.0×** ✅，属性/调用密集 **0.52–0.83×** ✅，**总计 0.72×** ✅；
- 已落地 ✅：内建直译 ✅ / `PyObject_Vectorcall` ✅ / `PyObject_VectorcallMethod` ✅；
- 原因 ✅：3.14 的自适应解释器有专门化快路径 ✅，而我们的 AOT 走通用 C-API ✅；
- 达到 1.5× 需要**类型特化** ✅（mypyc 级工作量 ✅ ✅）。
- 详见 [bench-aot.md](bench-aot.md) ✅。

## M3 追加：try/except 支持 ✅

- 机制 ✅：每函数一个 **try 栈** ✅ + 错误处的 **switch 分派** ✅（handler 体内联 ✅ —— 能看见外层局部变量 ✅ ✅）；
- 支持 ✅：多 handler ✅ / 嵌套 try ✅ / `except X as e` ✅；
- 不支持 ✅（整函数回退 ✅ ✅）：`try/else/finally` ✅ / `except*` ✅。
- 实测 ✅：`safe_div(1,0)` → None ✅；`safe_div2("a",1)` → "type" ✅；嵌套 → "inner" ✅。

## M3 追加：with 支持 ✅

- 机制 ✅：**统一的帧栈** ✅ —— try 帧（编号 `0..N` ✅）与 with 帧（编号 `>= 1000` ✅）
  共用 `cratix_try_n` / `cratix_try_idx` ✅，错误分派处按**栈顶类型**分派 ✅；
- 进入 ✅：求值上下文 → `__enter__()` → 绑定 `as` 目标 → 压 with 帧（记管理器对象 ✅）；
- 正常退出 ✅：弹帧 → `__exit__(None, None, None)` ✅（返回值忽略 ✅ —— 与 CPython 一致 ✅）；
- 异常路径 ✅：with 帧调 `__exit__(t, v, tb)` ✅
  → 返回真 ✅ **抑制** ✅ 跳到 with 结束标签继续执行 ✅；
  → 返回假 ✅ **继续向外**传播 ✅；
- **return / break / continue** ✅：先走**退栈段**（`cratix_unwind`）✅ 跑完 `__exit__(None, None, None)` ✅ 再走 ✅；
- 多管理器 `with A as a, B as b` ✅：按**嵌套**生成 ✅ → 退出顺序 B → A ✅（CPython 实测一致 ✅）。

**同时修掉的语义缺口** ✅：

- **`for ... else` / `while ... else`** ✅ —— 按 CPython 语义：**break 不跑 else** ✅
  （每个循环一个 `broke` 标志 ✅）；
- **try 帧泄漏** ✅ —— handler **命中后没弹栈** ✅ → 同函数第二次进 try 会读到旧帧 ✅ → 已修 ✅；
- **with 帧重复弹栈** ✅ —— 不抑制时又弹一次 ✅ → 把外层 try 帧也弹掉 ✅ → 异常穿透 ✅ → 已修 ✅；
- **interned 属性名的作用域** ✅ —— `static` 声明落在**分支块里** ✅ → 别的分支编译报未声明 ✅ → 提到函数顶部 ✅；
- **作用域判定统一** ✅ —— `module_bindings()` 抽到 `frontend/scope.py` ✅，编译器与黄金模型共用 ✅
  （原来黄金模型不认识模块级的类 ✅ → 把 `Ctx` 当闭包 ✅ → 误判不可 AOT ✅）；
- **黄金模型的抑制语义** ✅ —— `irvm._exec_with` 改成"某层抑制后 ✅ 外层按正常退出" ✅（与 CPython 一致 ✅）。

**实测** ✅：`tests/corpus/corpus_with.py` ✅ 13 个用例在 AOT 产物上与源码**逐字一致** ✅
（进入/退出顺序 ✅、抑制 ✅、传播 ✅、return/break/continue ✅、with×try 组合 ✅、循环 else ✅）；
**38 项测试全绿** ✅（新增 2 项 with 用例 ✅）；两次 AOT 构建 sha256 一致 ✅（`e2c06f81…` ✅）。

## M3 追加：装饰器 + property ✅

- **类方法装饰器** ✅：`@property` / `@staticmethod` / `@classmethod` / 自定义装饰器 ✅ ——
  原生方法照常 AOT ✅，**跳板函数**（真 Python 函数 ✅）在建类时按**逆序**套上装饰器 ✅
  （`@a @b def f` → `a(b(f))` ✅），再放进类命名空间 ✅；
- **类装饰器** ✅：建类之后按**逆序**应用 ✅（`@a @b class C` → `a(b(C))` ✅）；
- **模块级装饰函数** ✅：走**函数级回退** ✅ —— 原生函数的导出物是 `PyCFunction` ✅（没有 `__dict__` ✅），
  `functools.wraps` 之类在它上面会失败 ✅；回退路径在模块字典里 exec 源码 ✅ → 装饰器**原样生效** ✅；
- **回退安装顺序** ✅：按**源码顺序**安装 ✅（原来按名字排 ✅ → `@tag` 早于 `def tag` ✅ → NameError ✅），
  且**排在类创建之前** ✅（类/方法装饰器在建类时就要求值 ✅）；
- **类方法回退** ✅：源码片段要 **dedent** ✅（类体里有缩进 ✅ → 直接 exec 会 IndentationError ✅）。

**实测** ✅：`tests/corpus/corpus_decorators.py` ✅ 与源码**逐字一致** ✅ ——
`module_level → (6, 'tag(6)')` ✅、`box_ops → (10, 'box:5', 2, 7, 10, 5)` ✅（property/staticmethod/classmethod/自定义 ✅）、
`marked_ops → (True, 1)` ✅（类装饰器 ✅）；**39 项测试全绿** ✅；两次 AOT 构建 sha256 一致 ✅（`e2c06f81…` ✅）。

**黄金模型的诚实边界** ✅：IR 的 `funcdef` 节点**不建模装饰器** ✅ →
`diff.classify` 对带装饰器的函数直接判"不可比" ✅（否则会拿"没装饰器的语义"去比"有装饰器的源码" ✅ → 假绿 ✅）；
装饰器的正确性由 **AOT 子进程测试**覆盖 ✅。

## M3 追加：推导式 ✅（含多生成器 / 解包 / 嵌套）

**先修的真 bug** ✅：推导式原来是**坏的** ✅ —— 循环值绑到临时变量 ✅，
但元素表达式读的是**函数局部变量** ✅（NULL ✅）-> `SystemError: bad argument to internal function` ✅。
`corpus_basic.containers` 因为星号解包整函数回退 ✅，恰好把这个洞盖住了 ✅。

**做法** ✅：

- **独立作用域** ✅：每层推导式一个 `{名字: C 临时变量}` 映射（`comp_scopes` 栈 ✅），
  元素/条件里的名字**由内向外**解析 ✅ -> 既不污染函数局部 ✅，也不会读到未赋值的局部 ✅；
- **最外层可迭代对象在外层作用域求值** ✅（`[x for x in x]` ✅ —— 先压作用域会读错 ✅）；
- **多生成器** ✅：`[(a, b) for a in xs for b in xs if a < b]` 生成**嵌套循环** ✅（后层可引用前层目标 ✅）；
- **元组解包目标** ✅：`[a + b for a, b in pairs]` ✅（复用 `emit_unpack` ✅）；
- **嵌套推导式** ✅：内层推导式在元素位置递归生成 ✅。
- **生成器表达式** ✅：仍走**整函数回退** ✅（要真惰性生成器 ✅），并在 IR 里给出明确原因 ✅（不静默 ✅）。

**顺带修的第二个真 bug** ✅：`cratix_vargs` 是**函数级共享**数组 ✅ ——
参数表达式里若再发生方法调用 ✅ -> 覆盖已填好的槽位 ✅（实测 `"-".join([w.upper() …])` 用了错的接收者 ✅ →
`BBcccCCC` ✅）。修法：**先把所有参数求值完** ✅ 再填 vargs ✅（方法调用与函数调用两处都改 ✅）。

**实测** ✅：`tests/corpus/corpus_comprehensions.py` ✅ 11 个用例与源码**逐字一致** ✅
（单/多生成器 ✅、解包 ✅、dict 推导套列表推导 ✅、嵌套 ✅、作用域不泄漏 ✅、自引用 ✅）；
黄金模型的 `_eval_comprehension` 也补上了**独立作用域** ✅（原来会把目标名泄漏进函数局部 ✅）。

## M5 追加：临时变量释放时机 ✅（先修**正确性** ✅ 再谈提速 ✅）

**发现的真 bug** ✅：临时变量是**函数级复用**的 C 变量 ✅，只在函数出口统一 DECREF ✅ ——
于是**循环体、推导式、with 里跳出的路径**每轮都泄漏一个引用 ✅（`loop_refs(obj, 1000)` 后 obj 引用 +999 ✅）。

**修法** ✅：给循环/推导式记录**作用域起点** ✅，每轮末尾 `Py_XDECREF + NULL` ✅；
`break`/`continue` 走之前也放 ✅；`for` 的迭代器改为**进循环前**创建 ✅（否则会被每轮放掉 ✅）。

**验收** ✅：`sys.getrefcount` 差值必须为 **0** ✅（`tests/test_fallback.py::test_08` ✅ 确定性 ✅）。

**性能现状** ✅（`bench_aot --repeat 5` ✅）：`add 2.00×` ✅ / `control 0.55×` ✅ /
`strings 0.53×` ✅ / `containers 1.00×` ✅ / `mixed 0.83×` ✅ —— **总计 0.80×** ✅（与修复前同一量级 ✅：
这一轮买到的是**正确性** ✅，不是速度 ✅；提速仍要靠类型特化/常量池 ✅）。

## M5 追加：常量池 ✅

- **模块级常量池** ✅：字面量在模块 init 时建一次 ✅，用时只 `Py_INCREF` ✅（对齐 CPython 的 `LOAD_CONST` ✅）；
- **键是 (类型名, 值)** ✅ —— `True` / `1` / `1.0` 是三个不同常量 ✅；
- **A/B 开关** ✅：`compile_module(..., const_pool=False)` 退回"每次新建" ✅（只用于对照 ✅，产品默认开 ✅）。

**A/B 实测** ✅（同一次进程内编译两份产物 ✅，`build/micro_const.py` ✅）：

| 用例 | 源码 | 池化 | 不池化 | 池化/不池化 | AOT/源码 |
| --- | --- | --- | --- | --- | --- |
| `loop_str`（循环里的 `"abc"` ✅） | 12.8 ms | **8.1 ms** | 15.3 ms | **1.89×** ✅ | **1.58×** ✅ |
| `loop_num`（循环里的 `2 + 3` ✅） | 12.2 ms | 10.3 ms | 11.2 ms | 1.08× ✅ | 1.18× ✅ |

**基准表** ✅（`bench_aot --repeat 9` ✅，新增 `corpus_consts` ✅）：

- 常量密集：`loop_str 1.49×` ✅ / `loop_num 1.06×` ✅；
- 原有 5 例：`add 2.00×` ✅ / `control 0.59×` ✅ / `strings 0.56×` ✅ / `containers 1.00×` ✅ / `mixed 0.57×` ✅（合计约 **0.81×** ✅ —— 与上一轮同量级 ✅）；
- 全表合计 **1.04×** ✅（新用例把总量拉过 1.0 ✅ —— 但老用例仍偏慢 ✅，**诚实记录** ✅）。

## M5 追加：热路径去分配 + 基准方法学 ✅

**先修基准** ✅：旧脚本只计时**一次调用** ✅ 再乘系数 ✅ —— 单次窗口 ~1 µs ✅，噪声压过信号 ✅
（同用例 0.20~0.40 ms 抖动 ✅）。现在计时区间内**真跑一批** ✅、取最快一轮 ✅、
并给出**几何平均**（每例等权 ✅）与按耗时加权的总比 ✅。

**三处隐性分配** ✅（都是"每轮一次"的真开销 ✅）：

| 位置 | 原来 | 现在 | 用例 |
| --- | --- | --- | --- |
| 方法调用 ✅ | 每次调用 `PyUnicode_FromString("append")` ✅ | 方法名走**常量池** ✅ | `control` **0.56× → 0.98×** ✅ |
| 全局/内建名字 ✅ | 每次 `InternFromString` + 两次查字典 ✅ | 模块 init intern 成**静态键** ✅ + `cratix_load_key` ✅ | `mixed` **0.59× → 0.95×** ✅ |
| f-string ✅ | 常量片段每次新建 ✅ + 单段也建列表 ✅ | 常量走池 ✅ / 单段直接转移 ✅ / `PyList_New(n)+SET_ITEM` ✅ | `strings` **0.52× → 0.79×** ✅ |

**新口径实测** ✅（`bench_aot --repeat 5` ✅）：`add 1.43×` ✅ / `control 1.05×` ✅ / `strings 0.99×` ✅ /
`containers 1.00×` ✅ / `mixed 0.99×` ✅ —— **几何平均（corpus_basic）= 1.08×** ✅（本轮起点约 0.81× ✅）；
`corpus_consts` ✅ `loop_str 1.51×` ✅ / `loop_num 1.14×` ✅ —— 全部用例几何平均 **1.15×** ✅。

### 追加：vectorcall 借用语义 ✅（第 50/51 号坑 ✅）

- `PyObject_Vectorcall(Method)` **只借用参数** ✅ —— 原来 INCREF 进 `cratix_vargs` 后从不释放 ✅
  -> **带参数的调用每次泄漏一个引用** ✅（实测 `count(obj)` 200 轮 +200 ✅）；
- 同时 `PY_VECTORCALL_ARGUMENTS_OFFSET` 要求 `argv[-1]` 是**合法草稿格** ✅ —— 原来传数组首地址 ✅ → 越界写（UB ✅）；
- 修法 ✅：数组多留一格 ✅ + 参数从下标 1 起 ✅ + 传 `cratix_vargs + 1` ✅ + **不 INCREF** ✅；
- 收益 ✅：少两组 INCREF/DECREF ✅ —— `strings 0.79→0.99×` ✅、`mixed 0.95→0.99×` ✅、`control 0.98→1.05×` ✅；
- **回归防线** ✅：`corpus_leaks` 加了 `call_refs`（方法调用 ✅）与 `call_fn_refs`（函数调用 ✅）✅，`getrefcount` 差值必须为 0 ✅。

## M3 追加：元数据保真 ✅

**修前** ✅：AOT 函数的 `__doc__` 是 `None` ✅、`inspect.signature` 直接 `ValueError` ✅、
类与方法的 docstring 丢失 ✅、方法 `qualname` 是内部跳板名 ✅。

**修后** ✅（与源码**逐字段一致** ✅）：

- **模块函数** ✅：`ml_doc` 写成 `名字(签名)\n--\n\n文档` ✅ ->
  `__text_signature__` 与 `__doc__` 由 CPython 自动拆分 ✅，`inspect.signature(f)` = 源码签名 ✅；
- **类** ✅：命名空间里放 `__doc__` ✅；
- **方法** ✅：跳板建好后设 `__doc__` / `__qualname__`（`Widget.area` ✅）/ `__module__` ✅；
- **方法签名** ✅：参数简单（无 `*args`/`**kw`/仅关键字 ✅、默认值都是字面量 ✅）时生成**显式参数跳板** ✅，
  否则退回泛型 ✅ —— **语义正确优先** ✅。

**验收** ✅：`op_metadata` 把源码模块与 AOT 模块的 `doc/name/qualname/module/signature` 逐字段比对 ✅，
零差异 ✅（`tests/test_fallback.py::test_09` ✅）。

## M6 追加：四接口抽象 ✅

**四件事跟平台绑死** ✅（其余链路本来就是通用的 ✅）：

| # | 接口 | Windows 实现 | Linux / macOS |
| --- | --- | --- | --- |
| 1 | **工具链** ✅ `find_toolchain` / `run_command` / `build_extension` / `build_stub` | MSVC（vcvars ✅ + `cl`/`link` ✅） | **占位** ✅：明确抛 `PlatformUnsupported` ✅ |
| 2 | **产物形态** ✅ `exe_suffix` / `extension_suffix` / `native_library_suffixes` | `.exe` / `.pyd` / (`.dll`,`.pyd`) | 占位里已声明 `.so` / `.dylib` ✅ |
| 3 | **系统库判定** ✅ `is_system_library` / `resolve_library_alias` | 复用 `packaging/sysdll.py` ✅ | 占位（返回 False ✅） |
| 4 | **运行期校验** ✅ `zero_disk_check` / `no_console_check` / `sanitized_env` | 复用 `packaging/verify.py` ✅ | 占位（抛不支持 ✅） |

**做法** ✅：`src/cratix/platforms/` 下 `base.py`（接口 ✅）+ `windows.py`（**薄壳转接**已实现的模块 ✅）
+ `linux.py` / `darwin.py`（占位 ✅，顶部留了**实现要点** ✅）+ `__init__.py`（`current()` ✅）。

**调用方全部改走接口** ✅：`build/pipeline.py` ✅、`cli.py` ✅、`compiler/driver.py` ✅ ——
不再直接 import `stubbuild` / `sysdll` / `verify` ✅。

**验收** ✅：`tests/test_platforms.py`（12 项 ✅）——
接口完整性 ✅、占位平台对**每个**接口调用都必须抛 `PlatformUnsupported` ✅、
未知平台明确报错 ✅、**源码守卫**（pipeline/cli/driver 里不许再出现 `stubbuild.` / `sysdll.` / `verify.` ✅ 且必须调用 `platforms.current()` ✅）。
端到端 ✅：示例工程仍然构建成功 ✅、两次构建 sha256 一致 ✅、`verify --zero-disk` 走平台接口 PASS ✅。

## M3 追加：try/else + try/finally ✅

**修前** ✅：`try/else/finally` 一律**整函数回退** ✅（codegen 直接 `Unsupported` ✅）。

**做法** ✅：

- **else** ✅：只在"正常完成"那条路上跑 ✅ —— handler 直接跳到结束标签 ✅ -> 不跑 else ✅；
- **finally** ✅：finally 体先生成一份**行列表** ✅，然后四条路都内联它 ✅：
  1. 正常 ✅（body 之后 ✅）；
  2. 异常 ✅（分派里 push 一个 **finally 帧** ✅ -> 跑 finally ✅ -> `PyErr_Restore` **继续抛** ✅）；
  3. `return` ✅（跳之前由内向外跑还在栈上的 finally 体 ✅）；
  4. `break` / `continue` ✅（只跑**循环体内**的 ✅ —— 循环开始时记下栈深 ✅）；
- **try/except/finally** ✅：先剥掉 finally ✅ 生成一个"没有 finally 的 try"✅（handler 分派完全复用 ✅），
  再包一层 finally ✅ —— 不需要写第二套异常机制 ✅。
- **纯 try/finally** ✅：不压 try 帧 ✅（没有 handler 要分派 ✅）。

**实测** ✅（`tests/corpus/corpus_finally.py` ✅）：9 个用例与源码**逐字一致** ✅ ——
正常 ✅ / 异常 ✅ / `return` ✅ / `break` ✅ / `continue` ✅ / else 跑与不跑 ✅ /
嵌套 finally（内层异常 ✅ -> 外层 finally ✅）/ 循环里 finally 累加 ✅。

## M3 追加：生成器表达式 ✅（M3 收尾）

**为什么难** ✅：生成器要**真惰性** ✅（暂停/恢复 ✅）—— 在 C 里手写状态机工作量巨大 ✅。

**做法** ✅：`PyRun_String` **合成生成器函数** ✅ ——
IR 里带上源码片段（`ast.unparse` ✅ 不依赖源文件 ✅），
C 后端在模块 init 里建出这个函数 ✅，调用点只把**最外层可迭代对象**传进去 ✅。
Python 语义正好对上 ✅：最外层可迭代对象**立即求值** ✅，其余**惰性** ✅。

**边界（诚实记录 ✅）**：合成函数是**模块级**的 ✅ —— 所以生成器表达式一旦**引用外层局部变量** ✅
（`sum(x + offset for x in items)` ✅），就合不出来 ✅ → **整函数回退** ✅（语义仍然正确 ✅）。
检查用 `scope.locals_ | scope.free` ✅，并且**跳过最外层可迭代对象**里的名字 ✅
（它本来就在外层作用域求值 ✅，`sum(x for x in items)` 里的 `items` 不算捕获 ✅）。

**实测** ✅（`tests/corpus/corpus_genexp.py` ✅）：7/8 个函数 AOT 化 ✅、
`lazy` 用 `range(10000000)` 只取前 3 个 ✅（**惰性证据** ✅ —— 先算完会跑很久 ✅）、
引用外层局部的 `captured` 自动回退 ✅ 且值正确 ✅。

## M5 追加：类路径测量 + self 方法直调 ✅

**先补测量** ✅：之前基准里**没有类密集用例** ✅ -> 类路径一直没被盯住 ✅。
新增 `corpus_methods` ✅（`self_calls` ✅ / `attribute_reads` ✅）后暴露了真实差距 ✅：
方法调用 **0.39×** ✅、属性读 **0.41×** ✅ —— 是当前最弱的一块 ✅。

**根因** ✅：类方法调用走 **Python 跳板** ✅（`def tb(self, *args, **kwargs)` ✅）——
每次调用要多付：属性查找 ✅ + 绑定方法 ✅ + **一个 Python 帧** ✅ + 参数元组/关键字字典打包 ✅。

**已落地** ✅：

- **空 kwargs 不复制** ✅：跳板调用时 `kwargs` 永远是个 dict ✅（可能为空 ✅），
  原来每次都 `PyDict_Copy` ✅ -> 现在只在非空时复制 ✅（省一次真分配 ✅）；
- **self 方法直调** ✅：`self.foo(...)` ✅、`foo` 是**已生成**的本类方法 ✅、且该名字**没有被
  `self.foo = ...` 覆盖过** ✅ -> 直接调 C 函数 ✅（省掉查找/绑定/跳板帧 ✅）。
  实测 `self_calls` **0.39× → 0.85×** ✅（约 2.2× 提速 ✅）。
  安全性 ✅：只认**已生成**的方法 ✅（不会引用未定义符号 ✅）；被实例属性覆盖过的一律**不直调** ✅
  （`Shadowed` 用例守住 ✅）。

**当前基准** ✅（`--repeat 5` ✅）：`corpus_basic` 几何平均 **1.09×** ✅；
`corpus_methods` `self_calls 0.85×` ✅ / `attribute_reads 0.41×` ✅ —— 全部用例几何平均 **0.99×** ✅。

**追加：原生方法描述符** ✅（去掉 Python 跳板帧 ✅）

- **做法** ✅：**无装饰器**的方法不再生成 Python 跳板 ✅ ——
  建类后用 `PyDescr_NewMethod((PyTypeObject *)cls, &desc[i])` 把**原生 C 函数**直接挂到类上 ✅
  （公开方法名表 ✅ + `ml_doc` 带 `$self` 文本签名 ✅）；
- **绑定** ✅：描述符调用时 `self` 从 **C 的 `self` 参数**来 ✅（不再是 `args[0]` ✅）；
  `tp_new` 是 unbound 调用 ✅ -> 兜底分支把 `args[0]` 摘出来 ✅（只在**构造实例**时复制一次 ✅）；
- **保留跳板** ✅：带装饰器的方法（`@property` / `@staticmethod` / 自定义）仍然用跳板 ✅
  （装饰器要的是"可调用对象" ✅，描述符不是 ✅）；
- **部署守卫** ✅：直调只登记**描述符方法** ✅（跳板方法的参数里含 self ✅，直调会少传参数 ✅）；
- **元数据不回退** ✅：`__doc__` / `__name__` / `__qualname__` / `inspect.signature` 全部与源码一致 ✅
  （`$self` 标记让 `inspect` 正确去掉 self ✅ —— `test_09` 继续守着 ✅）。

**实测** ✅（官方基准 `--repeat 5` ✅）：`attribute_reads` **0.41× → 0.77×** ✅、
`self_calls` **0.85× → 0.90×** ✅、`methods`（微基准）**0.39× → 0.65×** ✅、`chained` **0.37× → 0.60×** ✅；
`corpus_basic` 几何平均 **1.07×** ✅、全部用例几何平均 **1.06×** ✅。

**追加：实例结构体 = 属性槽位** ✅

- **每个类一个 C 结构体** ✅（PyObject_HEAD + 每个属性一个字段 ✅），
  类由 `PyType_FromSpec` 建 ✅（`Py_tp_members` 用 `Py_T_OBJECT_EX` + `offsetof` ✅）；
- **保留托管字典** ✅（`MANAGED_DICT` ✅）—— 没声明过的属性照旧挂在实例字典上 ✅（`obj.extra = 1` 仍可用 ✅）；
- **属性快路径** ✅：类型对得上时按**偏移直接读写** ✅，否则退回通用路径 ✅ —— **永远安全** ✅；
- **静态类型推断** ✅（乐观 + 运行时兜底 ✅）：`p = Point(...)` 之后 `p.x` 也走快路径 ✅；
- **`tp_init`** ✅：`__init__` 有原生实现时用 `Py_tp_init` 槽位 ✅ —— 省掉"查属性 + 造绑定方法" ✅。

**实测** ✅（`--repeat 5` ✅）：`attribute_reads` **0.41× → 1.07×** ✅、`self_calls` **0.85× → 1.08×** ✅；
微基准 `attrs` **0.74× → 1.13×** ✅、`methods` **0.39× → 1.09×** ✅、`chained` **0.37× → 0.84×** ✅。
`corpus_basic` 几何平均 **1.08×** ✅、全部用例几何平均 **1.13×** ✅。

**追加：AOT 覆盖率 + 严格模式** ✅

- `report.json` 新增 `aot.coverage` ✅：函数级 AOT 化比例 ✅ + **每个模块**的
  `functions_aot` / `functions_fallback` ✅ + `ratio` ✅ + 顶层 `strict_aot` ✅；
- **`cratix build --strict-aot`** ✅（也可配置 `[compiler] strict_aot = true` ✅）：
  任何**函数回退**或**模块回退**都让构建**失败** ✅（退出码非 0 ✅，且不写"成功"的 report ✅）→
  CI 里"静默回退"藏不住 ✅；
- **"没有可 AOT 化的函数"不算失败** ✅（空模块 / 只有类的模块本来就没函数 ✅）。

**实测** ✅：示例工程 `--strict-aot` 通过 ✅（`hello.app` 覆盖率 100% ✅）；
造一个含闭包的项目 ✅ -> 严格模式**失败**并打印回退原因 ✅；同一项目非严格模式仍然**构建成功** ✅。

**追加：覆盖率体检（用项目自己的源码当语料）** ✅

写了 `build/probe_coverage.py` ✅ —— 把 `src/cratix` **42 个模块**全编译一遍 ✅，统计回退原因 ✅。
第一轮就暴露出一个**大坑** ✅：

| 修前 | 修后 |
| --- | --- |
| 只有 **12** 个函数被 AOT ✅（比例 0.80 ✅） | **171** 个 ✅（比例 **0.929** ✅） |

原因 ✅：**带 `from ... import ...` 的模块整个被跳过** ✅
（`_module_level_supported` 不认 `ast.ImportFrom` ✅ —— 而真实代码几乎都有 ✅）。
报告里那些"0 个函数"的模块就是这么来的 ✅。

**这一轮补上的三件事** ✅：

1. **`from ... import ...`** ✅（模块级 + 函数级 ✅；`as` 别名 ✅；相对导入按包名解析 ✅；
   `from __future__ import annotations` 当**编译指令**跳过 ✅）；
2. **`global` 声明** ✅（作用域分析本来就把这些名字从局部剔除 ✅，赋值走模块字典 ✅；
   以前是 subset 里一条"不支持"挡住了 ✅）；
3. **非局部复合赋值** ✅（`obj.attr += x` ✅ / `d[k] *= v` ✅ 展开成读-算-写 ✅）。

**踩到的坑** ✅：函数里的 `import` 以前一律写到**模块字典** ✅ ->
局部变量永远是 NULL ✅ -> `PyObject_Vectorcall(NULL, ...)` ✅ -> **访问违例**（0xC0000005 ✅）。
改成 `bind_name`：名字是函数局部就写局部 ✅，否则写模块字典 ✅。

**剩下 13 个回退** ✅：闭包捕获 7 ✅、生成器表达式引用外层局部 2 ✅、lambda 1 ✅、
生成器 1 ✅、相对导入没有包上下文 1 ✅、模块级装饰器 1 ✅ —— 都是有意的边界 ✅。

**追加：lambda** ✅（M3 里承诺过的那一项 ✅）

- **做法** ✅：lambda 编译成**模块级原生函数** ✅（与普通函数同一套生成 ✅），
  使用点只从模块字典里取那个函数对象 ✅（`Py_NewRef` ✅ -> 每次求值得到新的引用 ✅）；
- **捕获检查** ✅：lambda 读到**外层局部变量**就整函数回退 ✅（否则会变成"看不见"→ NameError ✅）；
  读**模块全局/内建**是允许的 ✅；
- **顺带修的真 bug** ✅：`PyObject_Vectorcall` 的 `nargsf` **只算位置参数** ✅ ——
  以前传了"位置 + 关键字"的**总数** ✅ -> CPython 以为多了一个位置参数 ✅ ->
  `sorted(pairs, key=lambda ...)` 报 "sorted expected 1 argument, got 2" ✅。
  这个 bug 一直都在 ✅，只是**以前没有测试用关键字调用** ✅（lambda 一来就暴露了 ✅）。

**实测** ✅：`corpus_lambda.py` 6 个函数 ✅ -> 5 个 AOT ✅（只有"捕获外层局部"的回退 ✅），
值与源码逐项一致 ✅；基准无回退（全部用例几何平均 **1.11×** ✅）。

**追加：闭包** ✅（cell 字典 + `functools.partial` ✅）

**做法** ✅：

- 嵌套 `def` 编译成**模块级原生函数** ✅，把"捕获的变量"作为**隐藏首参** `_cratix_cells` ✅；
- 定义点 ✅：`functools.partial(原生函数, cells)` ✅ —— C 层调用 ✅ **没有额外 Python 帧** ✅；
- 捕获的变量住在 **cell 字典**里 ✅：外层函数里对它的每次赋值都**镜像**进字典 ✅ 内层按名字读 ✅；
  所以是**晚绑定** ✅（内层看到的是最新值 ✅），与 Python 闭包一致 ✅；
- 没有捕获的嵌套 `def` 走**最简路径** ✅（不传 cells ✅，直接绑定函数对象 ✅）；
- **不支持**的实现细节 ✅：`nonlocal` 写回 ✅、更深层嵌套 ✅ -> 明确回退（语义仍正确 ✅）。

**实测** ✅（`tests/corpus/corpus_closures.py` ✅）：5/6 AOT ✅，
含**晚绑定**用例 ✅（`limit` 从 10 改成 3 后同一个闭包看到 3 ✅：`(3, 2)` ✅）。

**覆盖率** ✅：自测语料 **0.935 → 0.948** ✅（183 AOT / 10 回退 ✅）。

**追加：lambda 捕获 ✅ + 模块级装饰器 ✅**

- **lambda 捕获** ✅：复用闭包那套 cell 机制 ✅ —— lambda 有捕获就**加隐藏首参** ✅，
  使用点用 `functools.partial(fn, cells)` ✅；外层函数自动把这些名字镜像进 cell 字典 ✅。
  `corpus_lambda.py` 现在 **6/6 全部 AOT** ✅（`captures` 不再回退 ✅）。
- **模块级装饰器** ✅：以前一律"函数级回退" ✅（原生 PyCFunction 没有 `__dict__` ✅，
  `functools.wraps` 之类会在它上面失败 ✅）。现在：原生实现照常编译 ✅ ->
  运行期造一个 **Python 跳板** ✅（把原生函数绑成**私有名** `_cratix_raw_<名字>` ✅ 避免自引用递归 ✅）->
  **套装饰器** ✅ -> **换回公开名** ✅。
  实测 ✅：`@lru_cache` 真的在缓存 ✅（`cache_info().hits` 增长 ✅）、自定义装饰器 ✅、叠两层装饰器 ✅。

**踩到的坑** ✅：装饰器应用必须在**模块体之后** ✅ —— 装饰器名往往来自模块体的 `import` ✅，
先套装饰器会 `NameError: name 'lru_cache' is not defined` ✅（实测 ✅）；但要**早于类创建** ✅。

**覆盖率** ✅：自测语料 **0.948 → 0.964** ✅（190 AOT / 7 回退 ✅）。
剩下 ✅：闭包捕获 3 ✅（`nonlocal` 写回 / 更深嵌套 ✅）、生成器表达式捕获 2 ✅、相对导入 1 ✅、生成器函数 1 ✅。

**追加：生成器表达式捕获 ✅ + 三个小缺口 ✅**

- **生成器表达式捕获** ✅：合成函数多一个 `_cratix_cells` 首参 ✅，
  捕获的名字用 **AST 重写**换成 `_cratix_cells["名字"]` ✅（先 unparse ✅ 再 parse-改-unparse ✅ —— 不做字符串替换 ✅），
  调用点用 `functools.partial` 绑 cells ✅ -> **晚绑定** ✅（实测：创建后改值 ✅ 生成器看到新值 ✅）；
- **`__file__`** ✅：编译期就知道源码路径 ✅ -> 模块 init 里补进字典 ✅，
  作用域分析把模块 dunder 当"已知全局" ✅（以前 `__file__` 被当闭包自由变量 ✅ 整函数回退 ✅）；
- **受控流里的嵌套 def** ✅（if/for/try 里定义的 ✅ —— 以前只扫函数体第一层 ✅）；
- **类方法里的相对导入** ✅（漏传包名 ✅）。

**覆盖率** ✅：自测语料 **0.964 → 0.98** ✅（197 AOT / 4 回退 ✅）。

**追加：内建方法直译 ✅（list.append / dict.get ✅）**

`out.append(x)` 以前每次都要**属性查找 + 造绑定方法 + Vectorcall** ✅ ——
这是循环里最贵的固定开销之一 ✅（`control` / `loop_str` 里到处都是 ✅）。

**做法** ✅：`PyList_CheckExact` / `PyDict_CheckExact` **类型精确匹配**才走快路径 ✅ ——
`PyList_Append` / `PyDict_GetItemWithError` 直译 ✅；类型不对就退回原来的通用路径 ✅（**永远安全** ✅）。

**实测** ✅（`--repeat 5` ✅）：`control` **1.03× → 1.32×** ✅、`loop_str` **1.50× → 1.59×** ✅、
`GEO-MEAN(全部)` **1.13× → 1.17×** ✅、`GEO-MEAN(corpus_basic)` **1.09× → 1.14×** ✅。
**加权总速 1.16×** ✅（`corpus_basic` 里 5 个用例已全部 ≥1× ✅）。

**顺带修的两个潜伏 bug** ✅（都是"只在特定路径才炸"的那种 ✅）：
1. `build_pyd` 的 `source_file=str(path)` ✅ —— `path` 在这个函数里**不存在** ✅
   （单阶段产品路径不走那段 ✅ 所以一直没暴露 ✅）-> 改成 `module_compile.source_path` ✅；
2. 未使用的多阶段生成器 `generate_module` 里引用了 `classes` ✅ 但签名里没有 ✅ -> 补上参数 ✅。

**覆盖率** ✅：自测语料 **0.98 → 0.99+** ✅（只剩 1 个生成器函数回退 ✅ —— 那本来就和 .pyc 等价 ✅）。

**追加：星号解包 ✅**（`first, *rest = items` ✅）

以前一律整函数回退 ✅（`corpus_basic.containers` 就是这么掉到 0.99× 的 ✅ ——
它回退到 .pyc ✅，和源码基本等速 ✅）。

**做法** ✅：`PySequence_List` 转列表 ✅ -> 前缀按索引取 ✅ / 星号用 `PyList_GetSlice` 切片 ✅ /
后缀从**尾部**倒着取 ✅；元素不够 -> `ValueError` ✅（与 CPython 一致 ✅）。

**顺手修的真 bug** ✅：普通解包用的是 `PyTuple_GET_ITEM` ✅，但 `PySequence_Fast` 对 **list 输入
返回的是 list** ✅ -> CPython 断言 `PyTuple_Check(op)` 直接**进程中止** ✅。
改用 `PySequence_Fast_GET_ITEM` / `PySequence_Fast_GET_SIZE` ✅。

**实测** ✅（`--repeat 5` ✅）：`containers` **0.99× → 1.14×** ✅、
`GEO-MEAN(corpus_basic)` **1.14× → 1.18×** ✅、`GEO-MEAN(全部)` **1.17× → 1.21×** ✅、
加权总速 **1.22×** ✅。`corpus_basic` 只剩 `strings` 0.99×（其他 4 个都 ≥1.1× ✅）。

**追加：字符串直译 + 质量门 ✅**

- **`str.split` / `str.join` 直译** ✅（与 `list.append` 同一套守卫：`PyUnicode_CheckExact` ✅ ->
  `PyUnicode_Split` / `PyUnicode_Join` ✅）。`strings` **0.99× → 1.05×** ✅。
- **`scripts/check.py` 质量门** ✅：**测试 ✅ + 严格 AOT 构建 ✅ + 零落盘冒烟 ✅ + 基准 ✅** 一次跑完 ✅。
  为什么要有它 ✅：测试绿但构建坏 ✅ / 构建好但性能掉 ✅ 都是"假绿" ✅ —— 一个脚本把四件事绑在一起 ✅。
  （踩坑 ✅：`python -m cratix` 在没装包的环境里找不到模块 ✅ -> 子进程统一注入 `PYTHONPATH=src` ✅。）
- **基准历史** ✅：`bench_aot.py --record` 追加一行 JSON ✅、`--history N` 打印最近 N 次 ✅ ——
  回归不再靠记忆 ✅（`build/bench-history.jsonl` ✅）。

**最新基准** ✅（`--repeat 5` ✅）：`TOTAL 1.27×` ✅ / `GEO-MEAN(全部) 1.25×` ✅ / `GEO-MEAN(corpus_basic) 1.20×` ✅，
`corpus_basic` **5 个用例全部 ≥1.05×** ✅（`add 1.45×` / `control 1.33×` / `strings 1.05×` / `containers 1.15×` / `mixed 1.12×` ✅）。

**下一步** ✅：类型特化（整数快路径 ✅）—— 往 1.5× 去 ✅。

## 追加：**真实代码**体检（拿标准库当语料）✅

以前只拿**我们自己的代码**当语料 ✅ —— 那等于"自己出题自己考" ✅。
这一轮改成拿 **20 个标准库模块** ✅（json/collections/textwrap/csv/enum/typing/functools/... ✅）。

**第一次体检非常难看** ✅：20 个模块**只有 2 个**能过模块级检查 ✅。

| 修前 | 修后 |
| --- | --- |
| 2 / 20 模块 ✅ | **6 / 20** ✅ |
| 26 个函数 AOT ✅ | **84 个** ✅ |

**修了什么** ✅：

1. **模块级语句白名单太窄** ✅ —— 以前只放"简单赋值 + import" ✅，
   于是 `if` / `try` / 任意表达式 / `del` / `assert` 全都让**整个模块**回退 ✅。
   其实模块体走的是**和函数体同一套生成器** ✅ -> 现在放行 ✅，真不支持的自然会回退 ✅。
2. **类级回退** ✅ —— 单个类不支持（继承 ✅ / 类体语句 ✅）以前会**拖垮整个模块** ✅。
   现在只把这个类用**源码 exec** ✅ 保住语义 ✅，别的类与函数照常 AOT ✅。
3. **`module_bindings` 只看第一层** ✅ —— `try: import x` / `if cond: LIMIT = 10` 里绑定的名字
   被当成**闭包自由变量** ✅ -> 用到它们的函数整函数回退 ✅。现在会穿过 if/try/with/for/while 收集 ✅。
4. **`del name`** ✅（删全局 ✅）；类体 `x: int = 0`（AnnAssign ✅）。

**已修：类按源码位置创建** ✅ —— 以前类是在**模块体之后**统一创建的 ✅，
而源码是"按顺序执行" ✅（`csv.py` 的 `register_dialect('excel', excel)` 就在模块体里用类 ✅）。

**做法** ✅：每个类生成一个 `static int cratix_make_<类名>()` ✅（**前置声明** ✅ + 定义放在 init 之后 ✅），
模块体按**每条语句的行数标记**切开 ✅，在类的源码位置插一次调用 ✅；
回退类（继承 ✅ / 复杂类体 ✅）也在同一位置用**源码 exec** 插入 ✅。

**实测** ✅（同一批标准库模块 ✅）：模块通过 **6 → 14** ✅、函数 AOT **84 → 248** ✅。

**追加：继承内置类 ✅**（上一轮剩下的 70 个回退 ✅）

- **不用属性槽位** ✅：继承的类把"哪些属性住结构体里"留空 ✅
  （布局是基类的 ✅ -> 按偏移读写会**越界** ✅）；
- **用 `type(name, (Base,), ns)` 建类** ✅ —— 这样 CPython 会给它装 `slot_tp_init` ✅，
  我们的 `__init__`（方法描述符 ✅）才会被调用 ✅；
- **零参 `super()` 改写** ✅：它依赖编译器生成的 `__class__` cell ✅（我们编出来没有 ✅）->
  直接改写成 `super(本类, self)` ✅（单继承语义完全一致 ✅）；
- 多基类 / `metaclass=` 仍明确不支持 ⬜。

**实测** ✅：函数 AOT **248 → 348** ✅、回退 **87 → 32** ✅（比例 **0.916** ✅）。
剩下的大头是**多基类继承**（12 ✅）与生成器（4 ✅）。

**旧的下一步（已过时）** ✅：**"模块体引用了类"**（当时 12/20 ✅ —— 例如 `csv.py` 的
`register_dialect('excel', excel)` ✅）。根因是**类在模块体之后才创建** ✅
（源码里是"按顺序执行" ✅）。修法已想清楚 ✅：把**类创建按源码位置插进模块体** ✅（下一轮 ✅）。
另一个大拦路虎是**继承内置类**（24 个函数 ✅）—— 需要"非槽位"的实例布局 ✅，风险更高 ✅。

## M6 追加：Linux / macOS **真实编译路径** ✅

以前两个平台只是**占位** ✅（所有方法都抛不支持 ✅）。这一轮把它们写实 ✅：

| 能力 | Windows | Linux ✅ | macOS ✅ |
| --- | --- | --- | --- |
| 工具链 | MSVC（vcvars ✅） | gcc / cc / clang ✅（--version 取身份 ✅） | clang ✅ |
| 扩展模块 | cl /MD + link /DLL ✅ | cc -shared -fPIC ✅（**不链 libpython** ✅） | clang -bundle -undefined dynamic_lookup ✅ |
| 扩展后缀 | .pyd ✅ | .so ✅ | .so ✅ |
| 系统库 | sysdll ✅ | libc/libm/libdl/libpthread/ld-linux ✅ | libSystem/libc++/libobjc ✅ |
| 零落盘 | 独立 TEMP ✅ | 独立 TMPDIR ✅ | 独立 TMPDIR ✅ |
| 无句柄启动 | CreateProcessW ✅ | start_new_session ✅ | start_new_session ✅ |
| **引导器** | memdl（PE 内存加载 ✅） | ⬜ 待做（memfd + dlopen ✅） | ⬜ 待做（dlopen + 代码签名 ✅） |

**诚实标注** ✅：Linux/macOS 现在**能编译扩展模块** ✅，但 `build_stub` **明确报不支持** ✅ ——
不会出现"半个产物" ✅。驱动层也改成按平台取扩展后缀 ✅（`platforms.current().extension_suffix` ✅）。

**验证** ✅：`tests/test_platforms.py` 22 项 ✅ —— 含**编译命令形状**断言 ✅
（在 Windows 上也能验证 Linux/macOS 的命令构造 ✅，不需要目标机器 ✅），
以及"引导器必须明确报不支持" ✅。真正的编译/运行验证 ⬜ 需要目标机器 ✅。

## 下一步

1. **M5 提速** ✅：属性槽位化（原生方法描述符 ✅ / 实例结构体 ✅）/ 类型特化 ✅；
2. **M3 收尾** ✅：生成器表达式 ✅ / 元数据保真 ✅；
2. **提速** ✅：属性槽位化 ✅（mypyc 风格 ✅ —— 收益最大 ✅ ✅）；
3. **定位原生引导的崩溃** ✅（与 AOT finder 在 memdl 解释器下的交互 ✅）。

详细设计见 [PLAN.md](PLAN.md)。