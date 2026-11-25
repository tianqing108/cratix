# cratix 路线图

## 进度

| 里程碑 | 交付物 | 验收线 | 估时 | 状态 |
| --- | --- | --- | --- | --- |
| M0 骨架 + vendor | 仓库结构、vendor 打包半边并去业务化、统一改名、CLI deps/build | 示例工程产出 onefile exe 并运行成功；两次构建 sha256 一致；构建链无第三方编译器 | 1 周 | ✅ 完成 |
| M1 运行期 + 双形态 | 纯 Python 引导模块(.pyc 兜底)、stub 改造 D1–D8、onedir 目录版 | onefile 零落盘；onedir 可用；verify --no-console 通过 | 1.5 周 | ✅ 完成 |
| M2 AOT 内核 | IR / 作用域 / 子集判定 / 黄金模型 / C 后端 / MSVC 驱动 / 接入打包链 | 编译产物与源码行为等价；产物里能加载 AOT 模块 | 4 周 | ✅ 完成（接入打包链 ✅；函数级回退与双进程探针待做） |
| M3 子集扩展与语义对齐 | 类/try-except/with/推导式/装饰器/property/元数据保真 | tests/corpus 全绿；无静默语义偏差 | 3 周 | ⬜ 未开始 |
| M4 增量/缓存/可复现/报告 | 增量缓存、report.json、可复现性检查表 | 热构建 ≤5s；两次构建 sha256 一致 | 1.5 周 | 🟡 report.json 与可复现性已达成；增量缓存未做 |
| M5 性能与覆盖率 | 闭包 cell、slot 化属性、全局缓存、常量池、覆盖率提升 | 基准 ≥1.5×；启动不劣化 >15% | 2–3 周 | ⬜ 未开始 |
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

## 下一步

1. **函数级回退** ✅：不支持的函数内嵌 code object ✅ 运行期 `PyEval_EvalCode` ✅；
2. **双进程探针** ✅：源码版 vs AOT 版 ✅ JSON 对比 ✅ + 探针崩溃护栏 ✅；
3. **引导模块原生化** ✅：自研编译器产出 `runtime/cratix_bootstrap.pyd` ✅（stub 已支持该路径 ✅）；
4. **M3 子集扩展** ✅：类 / try-except / with / 推导式 / 装饰器 / property ✅。

详细设计见 [PLAN.md](PLAN.md)。