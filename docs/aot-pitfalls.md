# AOT 后端踩坑记录（v1）

这些是**只有真编译成 .pyd 才能暴露**的坑 ✅ —— 黄金模型（IR 解释器）抓不到 ✅
（它只验证前端语义 ✅，不涉及 C 代码生成 ✅ 与引用计数 ✅）。

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `SystemError: execution of module ... failed without setting an exception` | **exec 槽位的调用约定是 `int (*)(PyObject *)`** ✅ —— CPython 按 int 解释返回值 ✅；我们返回了 `PyObject*` ✅ → 指针被当作非零 int ✅ → "失败但没设异常" ✅ | exec 函数改成 `static int` ✅ 返回 0/-1 ✅ |
| 2 | `SystemError: module ...: m_size may not be negative for multi-phase initialization` | 多阶段初始化要求 `m_size >= 0` ✅（`-1` 是单阶段约定 ✅） | `m_size` 设为 0 ✅ |
| 3 | `ImportError: dynamic module does not define module export function` | 标准加载器按**模块名末段**找 `PyInit_<末段>` ✅ —— 我们用哈希符号 ✅ → 名字不匹配 ✅ | 开发期额外导出**别名符号** ✅（打包后由 `cratix_loader` 按 meta.json 显式调用 ✅ 不需要别名 ✅） |
| 4 | 扩展模块能加载但一调用就**访问违例**（0xC0000005） | 生成的声明 `PyObject *t0, *t1, ..., *t25 = NULL;` ✅ —— **只有最后一个被初始化** ✅ 其余是**野指针** ✅ → 清理时 `Py_XDECREF(野指针)` → 崩 ✅ | 每个临时变量**单独一行声明并初始化** ✅ |
| 5 | 偶发崩溃（用到 `**kwargs` 的函数） | `PyDict_GetItemWithError` 返回**借用引用** ✅ —— 我们对它做了 DECREF ✅ → 提前释放 ✅ → use-after-free ✅ | 借用引用**绝不 DECREF** ✅（要持有就显式 INCREF ✅） |
| 6 | 复合赋值 `t += x` 结果错误 | 生成的是 `var = rhs` ✅（把**右操作数**赋给了目标 ✅）而不是和 ✅；同时**泄漏**了和对象 ✅ | 结果落临时变量 ✅ 再 `Py_XSETREF` ✅（InPlaceAdd 可能原地返回同一对象 ✅） |
| 7 | `x is not None` 判定反了 | `is`/`is not` 的**失败条件**写反 ✅ —— 指针相等的判定方向搞错 ✅ | `is` 失败条件是"不等" ✅；`is not` 失败条件是"相等" ✅ |
| 8 | 扩展模块与解释器 CRT 不一致 | 用了 `/MT`（静态 CRT ✅）—— 解释器是 DLL CRT ✅ → 跨 CRT 分配/释放 ✅ → 堆损坏 ✅ | 扩展模块必须 `/MD` ✅（与解释器同一 CRT ✅） |
| 9 | `PyModuleDef` 初始化器字段数不对 | 给了 9 个值 ✅（PyModuleDef 只有 9 个字段 ✅）→ 编译错误 ✅ | 按字段数给值 ✅ |
| 10 | 内建名字取不到 | `PyEval_GetBuiltins()` 在 3.13+ 已软废弃 ✅ 且依赖当前帧 ✅（模块 exec 期可能没有 ✅） | `PyImport_AddModule("builtins")` + `PyModule_GetDict` ✅ |

## 调试手法（可复用 ✅）

1. **A/B/C 对照探针** ✅：把生成代码里的片段换成平凡实现 ✅ —— 二分出真正的触发因素 ✅；
2. **最小手写扩展对照** ✅：先确认"手写扩展能加载" ✅ 再怀疑生成代码 ✅；
3. **exit code 判崩溃类型** ✅：`-1073741819` = 0xC0000005 访问违例 ✅（Windows 错误弹窗会让进程"看起来卡住" ✅ —— 实际是崩了 ✅）；
4. **子进程隔离** ✅：每个用例一个子进程 + 超时 ✅ —— 一个崩了不影响其余 ✅。
## 追加：CPython 3.14 的扩展模块 ABI 事实（实测 ✅）

| # | 事实 | 影响 |
| --- | --- | --- |
| A | `PyModuleDef_Init(def)` 返回 **def 指针**（`Py_IS_TYPE(r, &PyModuleDef_Type)` == 1 ✅，`r == &def` ✅） | 加载器据此走**多阶段**路径 ✅（`PyModule_FromDefAndSpec2` ✅） |
| B | `PyModule_Create(def)` 要求 **m_size >= 0** ✅ | 我们用 `m_size = -1` ✅ → 不能用 ✅ |
| C | `PyModule_SetModuleDef()` 在 3.14 **没有导出** ✅ | 单阶段模块无法带 `m_def` ✅ → 标准扩展加载器拒绝 ✅（"did not return a valid extension module" ✅） |
| D | `PyImport_ImportModuleObject()` 在 3.14 **没有导出** ✅ | 改用 `PyImport_Import()`（收 `PyObject*` ✅） |
| E | `PyImport_ImportModule()` 收的是 **char\*** ✅ | 传 `PyObject*` ✅ → 指针被当字符串读 ✅ → 导入名变成垃圾 ✅ → "No module named '\x01'" ✅ |
| F | `ctypes.CDLL` 调用会**释放 GIL** ✅ | 调 Python C-API 会崩 ✅ → 用 `ctypes.PyDLL` ✅ |
| G | `PyEval_GetBuiltins()` 在 3.13+ 已软废弃 ✅ 且依赖当前帧 ✅ | 模块 exec 期可能没有帧 ✅ → 用 `PyImport_AddModule("builtins")` ✅ |

**因此我们的模块模型定为**：

```c
PyMODINIT_FUNC PyInit_cratix_<hash>(void) {
    PyObject *m = PyModule_New("hello.app");        /* m_size = -1 ✅ */
    PyModule_AddFunctions(m, cratix_methods);
    /* 模块体在这里执行 ✅（import / 常量 ✅）*/
    cratix_run_body(m);
    return m;                                      /* 返回**模块对象** ✅ */
}
```

并让 meta.json 标 `exec_done: true` ✅ → 引导模块**不再**调 `exec_def` ✅
（`PyModule_ExecDef` 要求 `m_size < 0` ✅ 且 init 返回**同一个**模块 ✅ —— 我们的 init 返回新模块 ✅ → 会报错 ✅）。
## 追加：函数级回退与探针

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 11 | 内嵌 code object 后加载崩溃（访问违例） | `PyMarshal_ReadObjectFromString()` 在 3.14 的行为与预期不符 ✅（实测崩溃 ✅） | 改为**内嵌函数源码** ✅ + 运行期 `Py_CompileString()` ✅（公开稳定 API ✅） |
| 12 | 回退函数调用时参数不对 | 源码被当成**模块**编译 ✅（`Py_file_input` ✅）→ 返回的是模块 code ✅ | 改成在**模块字典里 exec** ✅（`PyEval_EvalCode(code, globals, globals)` ✅）→ 函数的 `__globals__` 就是模块字典 ✅ |
| 13 | `PyFunction_New` 之后崩溃 | `PyFunction_New` **窃取** code 的引用 ✅ —— 多 DECREF 一次 ✅ → 悬空 ✅ | 不再 DECREF ✅（或改用 exec 路径 ✅ 彻底避开 ✅） |
| 14 | C 字符串里的中文变成乱码 | `c_string` 用 `\xNN` 转义非 ASCII ✅ —— UTF-8 多字节被逐字节截断 ✅ | 改为**按 UTF-8 字节 + 八进制转义** ✅（`\NNN` ✅ —— 避免与后续十六进制字符歧义 ✅） |
| 15 | 探针子进程没有输出 | worker 模块缺少 `__main__` 入口 ✅ | 补上 ✅（子进程用 `python -m` 跑 ✅） |
## 追加：类支持（v1）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 16 | 类方法调用报 "missing required argument: x" ✅ | **self 被前置了两次** ✅ —— Python 的 AST 里 `self` **本来就是普通参数** ✅ | 不要前置 ✅（参数表原样使用 ✅） |
| 17 | `PyDescr_NewMethod` 调用崩溃（访问违例）✅ | 3.14 的签名是 **`PyDescr_NewMethod(PyTypeObject *, PyMethodDef *)`** ✅ —— 类型在前 ✅；旧签名会把方法描述符当类型解引用 ✅ | 按新签名调用 ✅ |
| 18 | `PyType_FromSpec` 报 "dict 不是 PyType_Spec 的成员" ✅ | 3.14 的 `PyType_Spec` **没有 dict 成员** ✅ | 建完类再 `setattr` 挂方法 ✅ ✅ |
| 19 | `Point(3,4)` 报 "takes no arguments" ✅ | `object.__new__` **不接受参数** ✅ | 自定义 `tp_new` ✅（分配实例 ✅ 并调用 `__init__` ✅）✅ |
| 20 | 同一进程里加载**多个** AOT 模块后解释器崩溃 ✅（"Executing a cache" ✅） | 3.14 的**自适应字节码缓存** ✅ —— 同一 code object 重复执行 ✅ | AOT 测试改在**独立子进程**里跑 ✅（产品路径不受影响 ✅ —— 一个应用只加载自己的模块 ✅） |

**类支持的实现方式** ✅：

- 类 = `PyType_FromSpec` ✅（`Py_tp_new` ✅ + `Py_TPFLAGS_MANAGED_DICT` ✅ → 实例有 `__dict__` ✅）；
- 方法 = **原生函数** ✅（放在模块字典的私有名下 ✅）+ 一个**解释执行的薄跳板** ✅
  （`def m(self, *args, **kwargs): return _cratix_m(self, *args, **kwargs)` ✅）——
  普通 `PyCFunction` ✅ 在类型字典里**不会绑定 self** ✅，跳板负责传 ✅ ✅；
- 类属性 ✅ → 建类后 `setattr` ✅；
- 不支持：继承非 object ✅ / 元类 ✅ / `__slots__` ✅ / 装饰器 ✅ / `super()` ✅。
## 追加：引导模块原生化（dogfooding）✅

**成果** ✅：`cratix_bootstrap.py` ✅ 被**自研编译器**编译成 `runtime/cratix_bootstrap.pyd` ✅（11 个函数 AOT 化 ✅），
stub 通过**加载桥**装载 ✅（`cratix_loader.load(entry, init, spec)` ✅ —— 标准扩展加载器不适用 ✅，见下 ✅）。

| # | 现象 | 根因 | 现状 |
| --- | --- | --- | --- |
| 21 | 别名 init 分支丢掉类与回退函数 ✅ | `build_pyd` 重新生成 C 时**漏传** `fallback_code` / `classes` ✅ | 已修 ✅（按关键字传参 ✅）✅ |
| 22 | 两个类的 `__init__` 私有名互相覆盖 ✅ | 私有名没带类名 ✅ | 已修 ✅（类名限定 ✅）✅ |
| 23 | `PyType_FromSpec` 报错 ✅ | 3.14 的 `PyType_Spec` **没有 dict 成员** ✅ | 改回 `type(name, (object,), ns)` ✅ ✅ |
| 24 | **原生引导模块在 memdl 映射的解释器里崩溃** ✅（0xC0000005 ✅） | 未定位 ✅（原生应用模块 ✅ + .pyc 引导 ✅ 组合正常 ✅ ✅） | **原生引导改为可选** ✅（`CRATIX_BOOTSTRAP=native` ✅ 默认 `pyc` ✅）✅ |

**已知问题 #24 的隔离结论** ✅：

| 组合 | 结果 |
| --- | --- |
| 原生应用模块 + **.pyc 引导** ✅ | ✅ 正常 ✅ |
| 原生应用模块 + **原生引导** ✅ | ❌ 崩溃 ✅（崩在入口模块调用时 ✅ —— 引导本身加载成功 ✅ ✅）|
| .pyc 应用模块 + .pyc 引导 ✅ | ✅ 正常 ✅ |

**下一步定位方向** ✅：怀疑与 **memdl 手工映射的解释器**下的**堆/引用计数**交互 ✅（原生引导模块的类 ✅ 与 trampoline ✅ 会在解释器初始化后创建堆对象 ✅ ✅）。
## 追加：try/except 支持 ✅

**实现机制** ✅（C 没有异常 ✅ → 需要自己搭 ✅ ✅）：

1. 每个 try 块**压栈** ✅（记录编号 ✅ 与 handler 类型 ✅）；
2. 函数体出错 ✅ → 跳到 `cratix_error` ✅ → 取栈顶 ✅ → **switch 分派** ✅；
3. handler 体**内联**在分派里 ✅ —— 这样能看见外层局部变量 ✅ ✅；
4. handler 跑完 ✅ → 跳到该 try 的结束标签 ✅ → 继续执行 ✅；
5. 没有 handler 匹配 ✅ → 弹栈 ✅ → 继续外层 try ✅ → 都没有 ✅ → 真错误 ✅。

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 25 | 分派代码**丢失** ✅（异常没被捕获 ✅）✅ | `emit_try_handlers()` 把代码追加到 `gen.lines` ✅，而函数体**早已 join** ✅ → 分派没进函数 ✅ | 改成**返回行列表** ✅ 由 `generate_function` 拼进去 ✅ |
| 26 | handler 的临时变量**未声明** ✅ | handler 体在错误处才生成 ✅ → 临时变量登记太晚 ✅ | handler 体**在 try 处先生成** ✅（切到独立缓冲区 ✅）→ 临时变量及时登记 ✅ ✅ |
| 27 | 标签**重定义** ✅ | 返回了整个 `gen.lines` ✅ → 函数体被复制了一遍 ✅ | 只返回**新增**的行 ✅ |

## 追加：with 支持 + 语义对齐（v1）✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 28 | 带分支的函数编译报 `error C2065: cratix_an_v_name: 未声明的标识符` ✅ | interned 属性名的 `static PyObject *... = NULL;` 写在**首次使用的分支里** ✅ → 块作用域 ✅ → 别的分支看不见 ✅ | 声明**提升到函数顶部** ✅（`declared_attrs` 统一发声明 ✅） |
| 29 | `try` 包 `with` 时异常**跳过外层 try** ✅（一路冒到函数外 ✅） | `with` 帧不抑制时先 `break` ✅ → 落到统一 no-match 分支 ✅ → **又弹了一次栈** ✅ → 把外层 try 帧也弹掉 ✅ | 不抑制时**直接** `PyErr_Restore` + `goto cratix_error` ✅（帧已经弹过 ✅） |
| 30 | `__exit__` 调用报 `SystemError: NULL object passed to Py_BuildValue` ✅ | `PyObject_CallMethod(..., "OOO", t, v, tb)` 里 **traceback 常常是 NULL** ✅ → `Py_BuildValue` 不收 NULL ✅ | 传参前把 NULL 换成 `Py_None` ✅ |
| 31 | 同一函数里**第二次进 try** 行为异常 ✅（栈越积越多 ✅） | handler **命中后没弹栈** ✅（只在 no-match 分支弹 ✅）→ 栈顶一直是旧帧 ✅ | 命中即 `cratix_try_n--` ✅ + 释放 handler 类型 ✅ |
| 32 | `for ... else` 在 break 后**仍执行 else** ✅ | 生成的是"循环后直接跟 else 体" ✅ —— 少了一个 break 标志 ✅ | 每个循环一个 `broke` 标志 ✅ + 循环后 `if (!broke) { else 体 }` ✅ |

**with 的实现方式** ✅（与 try 共用一套栈 ✅）：

- **统一帧栈** ✅：try 帧编号 `0..N` ✅、with 帧编号 `>= 1000` ✅ —— 共用 `cratix_try_n` / `cratix_try_idx` ✅；
- **进入** ✅：求值上下文 → `__enter__()` → 绑定 `as` 目标 → 压 with 帧（记管理器对象 ✅）；
- **正常退出** ✅：弹帧 → `__exit__(None, None, None)` ✅（返回值忽略 ✅ —— 与 CPython 一致 ✅）；
- **异常路径** ✅：错误分派处按**栈顶类型**分派 ✅ —— with 帧调 `__exit__(t, v, tb)` ✅：
  返回真 → **抑制** ✅ → 跳到 with 结束标签**继续执行** ✅；返回假 → 异常**继续向外** ✅；
- **return / break / continue** ✅：先走**退栈段**（`cratix_unwind`）✅ 把 with 的 `__exit__(None, None, None)` 跑完 ✅；
- 多管理器 `with A as a, B as b` ✅：按**嵌套**生成（A 在外 ✅）→ 退出顺序 B → A ✅（与 CPython 实测一致 ✅）。

## 追加：装饰器 + property ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 33 | 装饰器**完全没生效** ✅（静默偏差 ✅） | 模块级函数编译成 `PyCFunction` ✅ —— 它**没有 `__dict__`** ✅ → `functools.wraps` 之类会失败 ✅；且 IR 的 `funcdef` 不建模装饰器 ✅ | 模块级装饰函数**走函数级回退** ✅（exec 源码 ✅ 装饰器原样生效 ✅） |
| 34 | 模块导入时 `NameError: name 'tag' is not defined` ✅ | 回退函数表按**名字排序**安装 ✅ → `composed`（用到 `@tag` ✅）排在 `tag` 之前 ✅ | 按**源码顺序**（`lineno` ✅）安装 ✅ |
| 35 | `IndentationError: unexpected indent` / `SyntaxError` ✅ | 装饰器片段用**行切片**取出 ✅ → 带上 `@` 与缩进 ✅；`Py_eval_input` 只收**一个表达式** ✅ | 用 `ast.get_source_segment` 取**表达式本体** ✅ + `dedent` ✅ |
| 36 | 建类时 `NameError`（装饰器还没定义 ✅） | 回退函数在**类创建之后**才安装 ✅ | 把回退安装**提到建类之前** ✅ |
| 37 | 类方法回退 `IndentationError` ✅ | 类体里的方法源码**带缩进** ✅ → 直接 exec ✅ | 回退片段 **dedent** ✅ |

## 追加：真实代码体检 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 92 | 标准库 20 个模块**只有 2 个**能过 ✅ | 模块级语句白名单只放"简单赋值 + import" ✅ -> `if`/`try`/表达式统统让**整个模块**回退 ✅ | 模块体本来就走同一套生成器 ✅ -> 放行 if/try/with/for/while/expr/assert/del ✅ |
| 93 | `try: import x` 里的名字被当成**闭包自由变量** ✅ | `module_bindings` 只看 `tree.body` 第一层 ✅ | 穿过 if/try/with/for/while 收集绑定 ✅（含 for 目标 ✅ / except 变量 ✅ / with as ✅） |
| 94 | 一个类不支持 ✅ -> **整个模块**回退 ✅ | 类检查失败直接 `module_ok = False` ✅ | **类级回退** ✅：把这个类的源码在 init 里 exec ✅，其余照常 AOT ✅ |
| 95 | 类体 `x: int = 0` 让模块回退 ✅ | 只认 `ast.Assign` ✅ | 同时认 `ast.AnnAssign`（有值且目标是简单名字 ✅） |
| 96 | `del name` 回退 ✅ | subset 白名单里有 `ast.Delete` ✅ | 放行 ✅（只允许删名字 ✅，删属性/下标仍回退 ✅） |
| 97 | 修完这些后 **12 个模块**报"模块体引用了类" ✅ | 类是在**模块体之后**统一创建的 ✅，而源码是"按顺序执行" ✅（例如 `csv.py` 的 `register_dialect('excel', excel)` ✅） | **下一轮**：把类创建按源码位置插进模块体 ✅（现在先**明确拒绝** ✅，不静默出错 ✅） |

## 追加：一个**否定结论** ✅（别再试第二遍 ✅）

| # | 尝试 | 结果 | 结论 |
| --- | --- | --- | --- |
| 91 | 自己给 `+` / `-` / `*` 加"精确 int 快路径" ✅（两次 `PyLong_AsLongLong` + 溢出检查 + `PyLong_FromLongLong` ✅） | **更慢** ✅：`loop_num` 1.30× -> **0.99×** ✅；`add` 1.45× -> 1.40× ✅ | **CPython 的 `PyNumber_Add` 对精确 int 已经高度优化** ✅ —— 在它上面再包一层只是多付两次转换 ✅。**要更快只能"不装箱"** ✅（局部变量用 C `long long` ✅ 跨语句保持 ✅），那是另一个量级的改动 ✅ |

已回滚 ✅（代码与 runtime 都恢复原样 ✅），结论留档 ✅。

## 追加：质量门与基准历史 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 90 | `scripts/check.py` 里构建/冒烟两步报 `No module named cratix` ✅ | 子进程是**干净的 Python 环境** ✅（cratix 没装进 site-packages ✅） | 给所有子进程注入 `PYTHONPATH=<repo>/src` ✅ |

## 追加：星号解包 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 88 | `a, b = some_list` 直接把进程**断言中止** ✅ | `PySequence_Fast` 对 list 输入返回 **list** ✅，而解包用了 `PyTuple_GET_ITEM` ✅ -> CPython 断言 `PyTuple_Check(op)` ✅ | 改用 `PySequence_Fast_GET_ITEM` / `PySequence_Fast_GET_SIZE` ✅（对 list/tuple 都对 ✅） |
| 89 | `first, *rest = items` 整函数回退 ✅（`containers` 因此只有 0.99× ✅） | 星号解包没实现 ✅ | `PySequence_List` + 前缀索引 + `PyList_GetSlice` 当星号 + 后缀倒取 ✅；元素不够 `ValueError` ✅ |

## 追加：内建方法直译 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 86 | `out.append(x)` / `d.get(k)` 在循环里很贵 ✅（`control` 只有 1.03× ✅） | 每次都**属性查找 + 造绑定方法 + Vectorcall** ✅ | 类型**精确匹配**才直译 ✅（`PyList_CheckExact` -> `PyList_Append` ✅、`PyDict_CheckExact` -> `PyDict_GetItemWithError` ✅），不匹配退回通用路径 ✅ -> control 1.32× ✅ |
| 87 | 快路径生成的 C 报 `cratix_an_v_append` 未声明 ✅ | 用了 `attr_ref` 却没先 `emit_attr_name` ✅（那条惰性声明没发出来 ✅） | 快路径开头先 `emit_attr_name(target)` ✅ |

## 追加：生成器表达式捕获 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 83 | 生成器表达式一旦引用外层局部就整函数回退 ✅ | 合成的是**模块级** Python 函数 ✅，看不见函数局部 ✅ | 合成函数多一个 `_cratix_cells` 首参 ✅；捕获名用 **AST 重写**成 `_cratix_cells["名字"]` ✅（不做字符串替换 ✅）；调用点 functools.partial 绑 cells ✅ |
| 84 | `__file__` 被当成闭包自由变量 ✅ | 模块 dunder 不在 module_bindings 里 ✅ | 作用域分析把模块 dunder 当已知全局 ✅；模块 init 里补上真的 `__file__` ✅ |
| 85 | if 里定义的嵌套 def 仍回退 ✅ | 只扫了函数体**第一层**语句 ✅ | `_nested_defs` 穿过 if/for/with/try 收集 ✅（不进更深的 def/lambda/class ✅） |

## 追加：模块级装饰器 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 81 | `@lru_cache` 的函数以前只能"函数级回退" ✅ | 原生函数是 PyCFunction ✅（没有 `__dict__` ✅），`functools.wraps` 之类在它上面会失败 ✅ | 原生实现照常编译 ✅ -> 造 **Python 跳板** ✅ -> 套装饰器 ✅ -> 换回公开名 ✅（跳板引用**私有名** ✅ 避免自引用递归 ✅） |
| 82 | 套装饰器时报 `NameError: name 'lru_cache' is not defined` ✅ | 装饰器应用放在**模块体之前** ✅，而 `lru_cache` 来自模块体的 `import` ✅ | 装饰器块挪到**模块体之后、类创建之前** ✅ |

## 追加：闭包 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 78 | 有了闭包支持，外层函数仍回退"闭包捕获（自由变量：value）" ✅ | 作用域分析的 `visit_FunctionDef` 直接 `generic_visit` ✅ -> **内层形参/局部**被算成外层的"读" ✅（与 lambda 同一个坑 ✅） | 访问后把"内层新读到的形参 + 内层新绑定的名字"从 reads 里剔掉 ✅ |
| 79 | 嵌套 def 编译时找不到 cells 字典 ✅ | `cell_names` 按"外层函数名"登记 ✅，但模型里按**内层名**建表 ✅ | 用 `_cratix_owner` 标出外层函数名 ✅ |
| 80 | 带捕获的嵌套 def 一调用就**访问违例** ✅ | cells 字典在**函数入口**预置所有捕获名 ✅ -> 函数体里才赋值的名字此刻是 **NULL** ✅ -> `PyDict_SetItemString(dict, key, NULL)` ✅ | 入口**只预置参数** ✅；函数体里的赋值由**赋值路径镜像**进字典 ✅ |

## 追加：lambda ✅（以及一个一直藏着的调用 bug ✅）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 76 | `sorted(pairs, key=lambda p: p[1])` 报 "sorted expected 1 argument, got 2" ✅ | `PyObject_Vectorcall` 的 `nargsf` **只算位置参数** ✅（关键字的值排在后面 ✅、个数看 kwnames ✅），我们传了"位置 + 关键字"的**总数** ✅ | 传 `n_pos` ✅。**这个 bug 一直都在** ✅，只是以前没有测试用**关键字调用** ✅（lambda 一上来就暴露 ✅） |
| 77 | lambda 能编译了，可**外层函数**还是回退"闭包捕获（自由变量：x）" ✅ | 作用域分析的 `visit_Lambda` 直接 `generic_visit` ✅ -> lambda 的**形参**被算成外层的"读" ✅ | `visit_Lambda` 先记形参 ✅、访问后只把**该 lambda 体内新读到的**形参名剔掉 ✅（外层同名变量不受影响 ✅） |

## 追加：`from ... import` / `global` / 非局部复合赋值 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 72 | 覆盖率体检发现**只有 12 个函数**被 AOT ✅（0.80 ✅），大量模块"0 个函数" ✅ | `_module_level_supported` **不认 `ast.ImportFrom`** ✅ -> 带 `from ... import ...` 的模块**整个**被跳过 ✅（真实代码几乎都有 ✅） | 支持 `ImportFrom` ✅（模块级 + 函数级 ✅、别名 ✅、相对导入按包名解析 ✅、`__future__` 当编译指令跳过 ✅）-> 12 → 171 个函数 ✅（0.929 ✅） |
| 73 | 函数里 `from os.path import basename` 之后一调用就**访问违例** ✅（0xC0000005 ✅） | import 一律绑定到**模块字典** ✅ -> 函数局部变量永远是 NULL ✅ -> `PyObject_Vectorcall(NULL, ...)` ✅ | 加 `bind_name` ✅：是局部就写局部 ✅，否则写模块字典 ✅ |
| 74 | `global X` 的函数仍然回退 ✅ | subset 里一条"不支持"✅（其实作用域分析早就把 global 名字从局部剔除了 ✅） | 删掉那条限制 ✅；`global`/`nonlocal` 建成 `noop` 节点 ✅（运行期无动作 ✅） |
| 75 | `obj.attr += x` / `d[k] += v` 回退 ✅ | codegen 只实现了"局部变量"的复合赋值 ✅ | 非局部目标展开成「读 ✅ + 算 ✅ + 写 ✅」（读/写各一次 ✅，与 CPython 一致 ✅） |

## 追加：构建日志与控制台编码 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 71 | 测试通过管道跑时 3 个用例全报 ERROR ✅，重定向到文件却全 OK ✅ | 构建日志含 ✅ 等非 ASCII ✅，**GBK 控制台/管道**下 print 抛 UnicodeEncodeError ✅ 直接打断构建 ✅ | build() 入口统一 reconfigure(utf-8, errors=replace) ✅（CLI 早已如此 ✅，库调用也要 ✅） |

## 追加：实例结构体 / 属性槽位 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 67 | 换成槽位后**堆被写坏** ✅（某个 dict 键尾巴变成 NUL ✅；PYTHONMALLOC=debug 报 "tail pad bytes not FORBIDDENBYTE" ✅） | MANAGED_DICT **必须配 HAVE_GC** ✅ —— 托管字典指针存在对象**前置头** ✅，没有 GC 标志就不预留那块空间 ✅ -> 成员偏移与托管字典**重叠** ✅ | 同时置 Py_TPFLAGS_HAVE_GC ✅ |
| 68 | 加了 HAVE_GC 后报 "has the Py_TPFLAGS_HAVE_GC flag but has no traverse function" ✅ | FromSpec 不会替你补 GC 槽位 ✅ | 生成 tp_traverse / tp_clear ✅（含 Py_VISIT(Py_TYPE(self)) ✅ 与 PyObject_VisitManagedDict / PyObject_ClearManagedDict ✅） |
| 69 | 结构体与类型对象**声明在函数体之后** ✅ -> C2065 ✅ | 生成顺序是"函数 -> 类" ✅ | 把 struct / 类型对象声明**插到 include 之后、函数体之前** ✅ |
| 70 | 造实例变慢 ✅（chained 0.37× ✅） | tp_new 里每次都 PyObject_GetAttrString(self, "__init__") ✅（查属性 + 造绑定方法 ✅） | 用 **Py_tp_init** 槽位 ✅（type_call 直接调 ✅）-> chained 0.84× ✅ |

## 追加：try/finally ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 64 | `finally` 里的语句在 `return` 路径上**不执行** ✅ | 我们只在"正常完成"处内联 finally ✅ | 生成一份**行列表** ✅，`return` / `break` / `continue` 之前由内向外内联 ✅ |
| 65 | 异常路径跑完 finally 后**异常被吞** ✅ | 分派里只执行 finally 体 ✅、没把异常还回去 ✅ | finally 帧的 case 里跑完 `PyErr_Restore(...)` + `goto cratix_error` ✅（**继续抛** ✅） |
| 66 | `break` 把**外层**的 finally 也跑了 ✅ | 直接跑整个 finally 栈 ✅ | 循环开始时记录**栈深** ✅ -> 只跑"循环体内"的 ✅ |

## 追加：原生方法描述符 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 62 | 把方法改成描述符后编译报 `C2065: cratix_tb_v_Point_norm2: 未声明的标识符` ✅ | `descriptors` 在**第一个** `for cls` 里算的 ✅，而 Python 的 for **不隔离作用域** ✅ -> 第二个循环用的是**上一个类**的集合 ✅（单类模块里就变成"全不跳过" ✅） | 在**每个** `for cls` 里各自算一次 ✅ |
| 63 | 描述符方法在私有方法表里**索引错位** ✅ | 私有 `PyMethodDef` 表已经跳过描述符 ✅，但建 `PyCFunction` 的循环仍按**全量**下标取 ✅ | 用一个**过滤后的列表**建 ✅（`trampoline_methods` ✅） |

**绑定约定** ✅：描述符调用时 `self` 在 **C 的 `self` 参数**里 ✅；`tp_new` 走的是 **unbound** 调用 ✅
（`self` 在 `args[0]` ✅）-> 绑定代码必须**两条路都支持** ✅：
`self != NULL` 直接用 ✅，否则把 `args[0]` 摘出来并把 `args` 左移一位 ✅（只在构造实例时分配一次 ✅）。

## 追加：类路径的两个开销 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 60 | 类方法调用只有 **0.39×** ✅ | 每个方法调用都走 **Python 跳板** ✅，而跳板会把 `kwargs` 显式传成**一个 dict** ✅（哪怕为空 ✅）-> 每次调用都 `PyDict_Copy` ✅ | `if (kwargs != NULL && PyDict_GET_SIZE(kwargs) > 0)` 才复制 ✅ |
| 61 | 同上 ✅（主要成本） | 跳板 = 一个**真 Python 帧** ✅ + 参数打包 ✅ + 绑定方法 ✅ | **self 方法直调** ✅：`self.foo(...)` 且 `foo` 是已生成的本类方法 ✅ 且未被 `self.foo = ...` 覆盖 ✅ -> 直接调 C 函数 ✅（`self_calls` **0.39× → 0.85×** ✅） |

**直调为什么只做 `self`** ✅：`obj.foo()` 的 `obj` 可能是**任意类型** ✅（也可能被实例属性遮蔽 ✅）；
`self` 的类在编译期是确定的 ✅ -> 只要静态确认"没有 `self.foo = ...`"就安全 ✅。
被覆盖过的方法**一律不直调** ✅（`corpus_methods.Shadowed` 用例守住 ✅）。

## 追加：生成器表达式 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 57 | 模块导入直接 `SystemError: globals must be a real dict` ✅ | 生成器辅助函数用 `PyRun_String` 建 ✅ —— 我把这段放在了 `cratix_globals = PyModule_GetDict(m)` **之前** ✅ | 挪到 globals/builtins/键都就绪**之后** ✅、模块体与类创建**之前** ✅ |
| 58 | 大部分生成器表达式仍然**整函数回退** ✅ | 捕获检查把**最外层可迭代对象**里的名字也算成了"引用外层局部" ✅（`sum(x for x in items)` 的 `items` ✅） | 最外层那个不检查 ✅ —— 它本来就在外层作用域求值 ✅ |
| 59 | 黄金模型跑 `captured`（`x + offset`）报 `NameError: offset` ✅ | `eval` 出来的生成器**没有闭包** ✅ —— 外层名字按**全局**查 ✅ | 求值作用域 = `globals + 外层局部` ✅（只给 locals 不行 ✅） |

## 追加：元数据保真 ✅（docstring / 签名 / qualname ✅）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 52 | AOT 函数的 `__doc__` 是 **None** ✅、`inspect.signature` 直接报 `ValueError` ✅ | `PyMethodDef.ml_doc` 传的是 `NULL` ✅；文本签名压根没生成 ✅ | 生成 `名字(签名)\n--\n\n文档` ✅ -> `__text_signature__` 与 `__doc__` 自动各归各位 ✅ |
| 53 | 加了签名后 `__text_signature__` 仍是 None ✅、`__doc__` 把整串都吐出来 ✅ | **名字前缀是硬要求** ✅：CPython 先比对 `doc[:len(name)] == name` 且紧跟 `(` ✅ —— 少了名字就解析失败 ✅ | ml_doc 前面加**函数名** ✅ |
| 54 | 类与方法的 `__doc__` 也是 None ✅、`qualname` 是 `_cratix_tb_Widget_area` ✅ | 类命名空间只放了 `__module__` ✅；跳板函数是动态生成的 ✅、没有 doc/qualname ✅ | 类 `ns` 里塞 `__doc__` ✅；跳板建好后设 `__doc__` / `__qualname__` / `__module__` ✅ |
| 55 | 方法签名显示 `(*args, **kwargs)` ✅ | 跳板一律是泛型 ✅（`inspect` 读的是跳板的 code object ✅） | 参数**简单**时（无 `*args`/`**kw`/仅关键字 ✅、默认值都是字面量 ✅）生成**显式参数**的跳板 ✅；否则退回泛型 ✅（**语义优先** ✅） |
| 56 | 显式跳板仍然退回泛型 ✅ | `builder` 把"没有默认值"填成 **None** ✅（`defaults` 和 `names` 等长 ✅）—— 我按"长度差"算默认值位置 ✅ -> 到处 None ✅ | 直接按**下标**取 ✅，`None` 视为"无默认值" ✅ |

**验收** ✅：`tests/corpus/corpus_metadata.py` + `tests/aot_worker.py::op_metadata` ✅ ——
源码模块与 AOT 模块的 `doc/name/qualname/module/signature` **逐字段比对** ✅，必须零差异 ✅。

## 追加：vectorcall 的**借用语义** ✅（又一次"每轮一次"泄漏 ✅）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 50 | 带参数的调用**每次泄漏一个引用** ✅（`[1,2,3].count(obj)` 跑 200 轮 → obj 引用 +200 ✅） | `PyObject_Vectorcall(Method)` **只借用参数** ✅ —— 我们却 `Py_INCREF` 进 `cratix_vargs` 后**从不释放** ✅（注释里还写着"会取走参数引用" ✅ 那是错的 ✅） | **不 INCREF** ✅：临时变量本来就持有引用 ✅，由函数/循环末尾的清理统一释放 ✅ |
| 51 | 潜在**越界写** ✅（未触发但确实 UB ✅） | 带 `PY_VECTORCALL_ARGUMENTS_OFFSET` 时 ✅ 被调方**可以用 `argv[-1]` 当草稿** ✅ —— 我们传的是数组首地址 ✅ → `argv[-1]` 越界 ✅ | 数组多留一格 ✅，参数从下标 1 开始写 ✅，传 `cratix_vargs + 1` ✅ |

**顺带的性能收益** ✅（同一次修复 ✅）：少了两组 INCREF/DECREF ✅ ——
`strings` **0.79× → 0.99×** ✅、`mixed` **0.95× → 0.99×** ✅、`control` **0.98× → 1.05×** ✅。

## 追加：热路径上的**隐性分配** ✅（M5 提速）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 46 | 基准同一用例在 0.20~0.40 ms 之间跳 ✅ —— 优化无从判断 ✅ | 基准只**计时一次调用** ✅ 再乘系数 ✅ —— 单次窗口 ~1 µs ✅ 噪声压过信号 ✅ | 计时区间内**真跑一批**（每例 1 万~30 万次 ✅）+ 取最快一轮 ✅ + 报**几何平均** ✅ |
| 47 | 方法调用密集的循环慢 ✅（`control` 0.56× ✅） | `emit_method_call` **每次调用**都 `PyUnicode_FromString("append")` ✅ —— 循环里就是每轮一次真分配 ✅ | 方法名走**常量池** ✅（`emit_const_piece` ✅）→ `control` **0.56× → 0.98×** ✅ |
| 48 | 取模块全局/内建名慢 ✅（`mixed` 0.59× ✅） | `cratix_load_name` 每次都 `PyUnicode_InternFromString` ✅ + 两次字典查找 ✅ | 编译期收集名字 ✅ → 模块 init 里 intern 成**静态键** ✅ → 运行时 `cratix_load_key` ✅ → `mixed` **0.59× → 0.95×** ✅ |
| 49 | f-string 慢 ✅（`strings` 0.52× ✅） | f-string 的**常量片段**绕过了常量池 ✅（每次 `PyUnicode_FromString` ✅）；单段也白建列表 ✅ | 常量片段走池 ✅ / 单段直接转移 ✅ / 多段 `PyList_New(n)+SET_ITEM` ✅ / 静态格式串走池 ✅ → `strings` **0.52× → 0.79×** ✅ |

## 追加：常量池 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 44 | 加了常量池后整份 C **编译失败** ✅：`error C2143: 语法错误: 缺少"{"（在"*"之前）` + `C2065: "NULL": 未声明的标识符` | 池变量的 `static PyObject *...` 被插到了 **`#include <Python.h>` 之前** ✅ —— 那时 `PyObject` / `NULL` 都还没定义 ✅ | 声明**插到最后一个 `#include` 之后** ✅（文件作用域 ✅ 且类型可见 ✅） |
| 45 | 常量池把 `True` 与 `1` 混成一个 ✅ | 键只用**值** ✅（`hash(True) == hash(1)` ✅ 且 `True == 1` ✅） | 键改成 **(类型名, 值)** ✅ |

**为什么要池化** ✅：CPython 解释器是 `LOAD_CONST`（直接取 `co_consts` ✅ 零分配 ✅）；
不池化的话 ✅ 循环里每轮都在 `PyLong_FromLongLong` / `PyUnicode_FromString` ✅ ——
字符串/bytes 常量是**真分配** ✅（A/B 实测 `loop_str` **1.89×** 差距 ✅）。
产品默认**开** ✅；`compile_module(..., const_pool=False)` 只用于 **A/B 对照** ✅。

## 追加：临时变量的**释放时机** ✅（长循环泄漏 ✅）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 41 | 长循环里**内存线性增长** ✅（`loop_refs(obj, 1000)` 后 obj 的引用 +999 ✅） | 临时变量是**函数级复用**的 C 变量 ✅ —— 每轮直接覆盖 ✅ -> **每轮泄漏一个引用** ✅（只在函数出口统一 DECREF ✅） | 记录**循环体起点** ✅，每轮末尾 `Py_XDECREF + NULL` ✅；`break`/`continue` 之前也要放 ✅ |
| 42 | 推导式同样每轮泄漏 ✅（`[obj for _ in xs]` 后 +n ✅） | 元素/条件的临时变量在**整段推导式之后**才释放 ✅ | 每层生成器一个**迭代起点** ✅，每轮末尾释放 ✅；过滤 `continue` 前也放 ✅ |
| 43 | `with` 里 `continue`/`break` 的循环每次都泄漏**管理器对象** ✅ | 正常退出路径会 DECREF 管理器 ✅，但 `break`/`continue` 走的是**退栈段** ✅ —— 那里只看借用引用 ✅ 不放 ✅ | 循环体记录**辅助变量起点** ✅，每轮末尾一并释放 ✅（`for` 的迭代器改为**进循环前**创建 ✅ 才不会被误放 ✅） |

**验收方式** ✅：`tests/corpus/corpus_leaks.py` ✅ + `sys.getrefcount` 差值**必须为 0** ✅
（确定性 ✅ 不靠内存采样 ✅）；`tests/test_fallback.py::test_08` 守在 CI 里 ✅ ✅。

## 追加：推导式 ✅

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 38 | 推导式一调用就 `SystemError: bad argument to internal function` ✅（object.c ✅） | 循环值绑到**临时变量** ✅，但元素/条件里的名字解析成**函数局部**（从未赋值 ✅ = NULL ✅）→ 把 NULL 交给 `PyNumber_Multiply` ✅ | 推导式建**独立作用域** ✅：目标名 → 专用临时变量 ✅，读名字**由内向外**查 ✅ |
| 39 | `"-".join([w.upper() for w in words])` 返回 `BBcccCCC` ✅（分隔符变成了 `ccc` ✅） | `cratix_vargs` 是**函数级共享**数组 ✅；方法调用**先填 vargs[0] ✅ 再求参数** ✅ -> 参数里的嵌套调用**覆盖**了接收者槽位 ✅ | **先把所有参数求值完** ✅ 再填 vargs ✅（方法调用 + 函数调用两处 ✅） |
| 40 | 推导式改写外层同名变量 ✅（`x = 100; [x for x in xs]` 之后 x 变了 ✅） | 推导式目标直接写函数局部 ✅ | 目标只写推导式作用域的临时变量 ✅；**最外层可迭代对象**仍在外层作用域求值 ✅ |

**类方法的装饰器怎么做** ✅：原生方法照常 AOT ✅ → 生成**跳板函数**（真 Python 函数 ✅）→
在建类时按**逆序**调用装饰器 ✅（`PyRun_String(expr, Py_eval_input)` 求值 + `PyObject_CallOneArg` 应用 ✅）→
结果放进类命名空间 ✅。于是 `property` / `staticmethod` / `classmethod` / 自定义装饰器**全部走同一机制** ✅。

**黄金模型同步** ✅：`irvm._exec_with` 改成 CPython 的**抑制语义** ✅ ——
某层抑制后 ✅ 外层按**正常退出**收 `__exit__(None, None, None)` ✅（原来会把异常继续传给外层 ✅ ✅）。
**作用域判定统一** ✅：`module_bindings()` 抽到 `frontend/scope.py` ✅ ——
编译器与黄金模型共用 ✅（原来黄金模型不认识模块级的类 ✅ → 把 `Ctx` 当闭包 ✅ → 误判不可 AOT ✅）。