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

**黄金模型同步** ✅：`irvm._exec_with` 改成 CPython 的**抑制语义** ✅ ——
某层抑制后 ✅ 外层按**正常退出**收 `__exit__(None, None, None)` ✅（原来会把异常继续传给外层 ✅ ✅）。
**作用域判定统一** ✅：`module_bindings()` 抽到 `frontend/scope.py` ✅ ——
编译器与黄金模型共用 ✅（原来黄金模型不认识模块级的类 ✅ → 把 `Ctx` 当闭包 ✅ → 误判不可 AOT ✅）。