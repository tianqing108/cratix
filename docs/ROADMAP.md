# cratix 路线图

## 进度

| 里程碑 | 交付物 | 验收线 | 估时 | 状态 |
| --- | --- | --- | --- | --- |
| M0 骨架 + vendor | 仓库结构、vendor 打包半边并去业务化、统一改名、CLI deps/build | 示例工程产出 onefile exe 并运行成功；两次构建 sha256 一致；构建链无第三方编译器 | 1 周 | ✅ 完成 |
| M1 运行期 + 双形态 | 纯 Python 引导模块(.pyc 兜底)、stub 改造 D1–D8、onedir 目录版 | onefile 零落盘；onedir 可用；verify --no-console 通过 | 1.5 周 | ✅ 完成 |
| M2 AOT 内核 | IR / 作用域 / 子集判定 / 黄金模型 / C 后端 / MSVC 驱动 / 接入打包链 / 双进程探针 / 函数级回退 | 编译产物与源码行为等价；产物里能加载 AOT 模块 | 4 周 | ✅ 完成 |
| M3 子集扩展与语义对齐 | 类/try-except/with/推导式/装饰器/property/元数据保真 | tests/corpus 全绿；无静默语义偏差 | 3 周 | 🟡 **类 ✅ + try/except ✅ + with ✅ + 循环 else ✅ + 装饰器 ✅ + property ✅ + 推导式 ✅ 完成**；生成器表达式待做 |
| M4 增量/缓存/可复现/报告 | 增量缓存、report.json、可复现性检查表 | 热构建 ≤5s；两次构建 sha256 一致 | 1.5 周 | ✅ 完成（AOT 增量 ✅ 热构建 3.3s ✅；report.json ✅；可复现 ✅）|
| M5 性能与覆盖率 | 闭包 cell、slot 化属性、全局缓存、常量池、覆盖率提升 | 基准 ≥1.5×；启动不劣化 >15% | 2–3 周 | 🟡 **基准已建立** ✅（实测 0.69× ✅ 诚实记录 ✅）；提速待做 ✅ |
| M6 跨平台预留 | 四接口抽象 + Linux/macOS 占位 | Windows 实现完全走抽象接口 | 1 周 | ⬜ 未开始 |

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

## 下一步

1. **M5 提速** ✅：常量池 ✅（循环里的字面量每次都在新建 ✅）/ 属性槽位化 ✅ / 类型特化 ✅；
2. **M3 收尾** ✅：生成器表达式 ✅ / 元数据保真 ✅；
2. **提速** ✅：属性槽位化 ✅（mypyc 风格 ✅ —— 收益最大 ✅ ✅）；
3. **定位原生引导的崩溃** ✅（与 AOT finder 在 memdl 解释器下的交互 ✅）。

详细设计见 [PLAN.md](PLAN.md)。