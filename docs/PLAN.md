# cratix —— 自研 Python 打包工具实施计划

> 决策来源（已确认）：**编译部分自研 Python→C (AOT)**（不依赖 Cython/mypyc 等外部编译器）· **onefile + onedir 双模式** · **打包半边 vendor 自 `C:/project/sports-server/tools/cypack`** · **Windows 先行，接口预留跨平台**。
> 环境已核验：CPython 3.14.7（`C:/Program Files/Python314`，含 `include/Python.h`、`libs/python314.lib`）、MSVC 18 Community（cl 14.50.35717 / ml64）、`compression.zstd` 可用；`C:/project/cratix` 为空 git 仓库。

---

## 0. 目标与成功标准

**目标**：交付自研 Python 打包工具 `cratix`：自研 AOT 编译器把 Python 模块编译成原生扩展；打包半边复用 cypack 已验证的单文件技术栈；产出 **onefile（单 exe、运行期零落盘）** 与 **onedir（目录发行版）**。

**成功标准（v1 交付口径）**
1. `python -m cratix build` 产出 onefile 与 onedir 两个产物，均可独立运行、退出码 0。
2. onefile 运行期 **零落盘**（TEMP 与 %LOCALAPPDATA% 前后无新增文件，校验文件名集合 + 内容哈希）。
3. 构建链 **不依赖任何第三方 Python 编译器**（Cython / mypyc / mypy / PyPy 等），构建期只需 CPython + MSVC。
4. AOT 产物与源码版行为等价（差分探针 0 差异），性能优于 .pyc 形态（基准 ≥1.2×）。
5. 连续两次构建产物 **sha256 一致**（含 `PYTHONHASHSEED` 变化、跨机器）。
6. 子集外部分 **自动回退**（函数级优先，模块级兜底），构建仍成功，回退原因逐模块写入 `report.json`。
7. **引导模块自身由自研编译器编译成原生 `.pyd`**（dogfooding），启动期引导阶段 ≤40 ms。

---

## 1. 显式假设

1. **“不依赖外部” 的边界**：不依赖任何第三方 Python 编译器；允许 Python 标准库（`ast`/`tokenize`/`marshal`/`compile`/`tomllib`/`zipfile`）与 **CPython 官方 C-API 头文件 + `python314.lib`**（CPython 自身 ABI，非第三方依赖）。若连 C-API 也不允许，Windows 上的 Python→C AOT 不可实现（须自研对象模型/解释器），需重新决策。
2. **目标解释器 = 构建解释器 = CPython 3.14 x64**；AOT 产物与该解释器 ABI 绑定（与 Cython 同约束）。
3. 打包目标平台 **Windows x64**；Linux/macOS 本期只做接口预留（M6）。
4. **第三方原生扩展**（`ext3rd/`、`extstd/`）在 onefile 走 memdl 内存加载；cypack 已记录“手工映射的 C/Rust 扩展可能因静态 TLS 崩溃”，故提供 `--native-mode=mem|disk` 开关，并在文档给出支持矩阵。
5. **v1 不支持 Qt/PySide6**：vendor 时剥离 cypack 中 PySide6/shiboken6 的特判逻辑；如后续需要，另立里程碑按 cypack 结论重做。
6. **不携带 sports-server 业务裁剪**；裁剪表由 `cratix.toml` 驱动、默认空。
7. **许可与分发**：保留 zstd（BSD-3-Clause）LICENSE 与 LZMA SDK（**公有领域**）；MSVC runtime 走官方 redistributable（打包器不自动附带，由使用方承担再分发义务）。
8. cratix 与 sports-server **无运行时耦合**；`docs/vendor-sources.md` 记录逐文件来源与本地改动。

---

## 2. 目标架构与仓库布局

```
C:/project/cratix/
├─ pyproject.toml            # setuptools 后端, src 布局, 无第三方构建依赖
├─ README.md  docs/          # architecture / aot-compiler / payload-format / roadmap / vendor-sources
├─ src/cratix/
│  ├─ __init__.py  __main__.py  cli.py  config.py  paths.py  errors.py
│  ├─ packaging/             # ★ vendor 自 cypack（改名/泛化/剥离）
│  │   ├─ deps.py  pe.py  payload.py  assemble.py  stubbuild.py  verify.py
│  ├─ compiler/              # ★ 自研 AOT 编译器（核心新增）
│  │   ├─ ir.py
│  │   ├─ frontend/  scope.py subset.py rewrite.py
│  │   ├─ backend/   codegen.py  runtime.h  runtime.c
│  │   ├─ golden/    irvm.py        # O2b：IR 黄金模型（先解释执行，后生成 C）
│  │   ├─ driver.py  pyc.py  probe.py
│  ├─ build/         pipeline.py  report.py
│  └─ runtime/       cratix_bootstrap.py     # ★ 引导模块源码（Python 单一来源）
├─ src/cratix/stub/          # ★ vendor 自 cypack 的 C 源码（改造）
│  ├─ stub.c memdl.c/.h zipread.c/.h vfs.c/.h tlsfix.c/.h tlsfix.asm diag.c/.h
│  └─ zstd/…                 # （删除 xz/LzmaDec）
├─ examples/hello/
└─ tests/  test_*.py  e2e/  corpus/  bench/
```

**平台接口（跨平台预留）**
```python
class Compiler(Protocol):
    def compile_module(self, src: Path, out_dir: Path, opts: BuildOptions) -> CompileOutcome: ...
class Packer(Protocol):
    def write_payload(self, entries: dict[str, bytes], meta: dict) -> Path: ...
    def assemble(self, stub: Path, payload: Path, out: Path) -> Path: ...
class StubBuilder(Protocol):
    def build(self, build_dir: Path, opts: BuildOptions) -> Path: ...
class RuntimeBootstrap(Protocol):
    def source(self) -> Path: ...
    def entry(self) -> str: ...          # cratix_bootstrap
```
Windows 先实现；Linux/macOS 留占位（M6）。

---

## 3. 子系统 A｜vendor 打包半边（cypack → cratix）

**拷贝来源**：`sports-server/tools/cypack/{deps.py, pe.py, payload.py, assemble.py, stubbuild.py, trims.py, manifest.py, native.py, verify_no_console.py, verify_zero_disk.py}` 与 `stub/**`（C 源码 + zstd）。

**逐文件保留/删除/改写清单**
| 源文件 | 处理 |
| --- | --- |
| `deps.py` | **保留**（AST 闭包），去 `build/deps_imports.json` 硬编码路径，缓存键加编译器版本 |
| `pe.py` | **保留**（PE 导入/导出解析，边界夹紧逻辑必须保留） |
| `payload.py` | **保留骨架**，**删除** PySide6/shiboken6 特判、删 `PROJECT_TOPS` 业务列表；保留 `collect_stdlib`/`collect_stdlib_extensions`/`collect_project`/`collect_native`/`native_dependencies`/`precompress_*`/`apply_solid_groups`/`write_payload` 的全部安全与可复现逻辑 |
| `assemble.py` | **保留**，cookie magic `CYPK1`→`CRTX1`；onefile 内嵌追加，onedir 走旁置写出 |
| `stubbuild.py` | **保留**，源码目录改指 `src/cratix/stub`；指纹（相对路径+mtime_ns+size+sha256+vcvars 身份+图标）保留 |
| `trims.py` | **删除**，改为 `cratix.toml` 驱动（默认空） |
| `manifest.py` | **保留**，REQUIRED/FORBIDDEN 改配置驱动，缺省不检查 |
| `native.py` | **保留**，DLL 清单改配置驱动 |
| `verify_no_console.py` / `verify_zero_disk.py` | **保留 + 改名** `verify` 子命令；zero-disk 加严为“文件名集合 + 内容哈希 + %LOCALAPPDATA% 检查” |
| `stub/**` | **保留全部 C 源码**（memdl/vfs/tlsfix/zipread/diag/zstd/xz-LZMA）；**仅做** §6 的 D1–D8 改造 |

**统一改名**：`cypack_loader`→`cratix_loader`、cookie `CYPK1`→`CRTX1`、环境变量 `CYPACK_*`→`CRATIX_*`、构建目录 `build/cypack`→`build/cratix`。

**必须保留的 cypack 不变量**（写入 `docs/architecture.md` 并在代码注释标注来源）
1. 仓库源码零改动（改写只发生在暂存副本）；2. 每个包的 `__init__.pyc` 必须进 zip；3. **回退清单在探针前算好**，探针失败也进回退；4. zstd 缓存复用前提是 `/Brepro`；5. `zipfile` 只认 STORED/DEFLATE → `.pyc` 用 DEFLATE、二进制走预压缩 zstd 直通；6. 构建结束必须输出总耗时 + 各阶段耗时 + 逐模块分类。

---

## 4. 子系统 B｜自研 AOT 编译器（核心新增工作量）

**技术路线**：**CPython C-API 后端**——生成的 C 调用 `Python.h`，链接 `python314.lib`。收益：局部变量用 C 局部（不进 frame 字典）、类属性用生成的 `PyGetSetDef` 槽位、常量折叠、全局读取一次性查 `__dict__`。**不追求 mypyc 级性能**，目标 1.2–2.5×。

**内部开发顺序（O2b：先黄金模型后代码生成）**
1. `frontend`：ast → 自有 IR（`compiler/ir.py`），作用域/自由变量分析，**子集判定**。
2. `golden/irvm.py`：**IR 解释执行器**——把 IR 直接当字节码解释跑，与源码版差分比对。此步不依赖 MSVC，把“语义 bug”与“C 生成 bug”分离。
3. `backend/codegen.py`：IR → C。
4. **逐层验证**：IR-VM 与源码等价 → 生成的 C 与 IR-VM 等价 → 生成的 C 与源码等价。

**编译流水线（单模块）**
1. 读源码（`tokenize.open`，尊重 PEP 263）；`ast.parse` 失败 → 回退。
2. 前端 + 变换（`frontend/rewrite.py`）：常量折叠、`if True/False` 分支消除、死代码删除、`__all__` 归一。
3. IR 构建。
4. 后端（`backend/codegen.py` + `backend/runtime.h/.c`）：
   - 每模块一个 `.c`：`PyModuleDef`（`m_size>0` 承载只读全局缓存）+ 导出 `PyInit_cratix_<sha1(module)[:10]>`；
   - 每顶层函数一个 `static PyObject *fn_xxx(PyObject*, PyObject *args, PyObject *kwargs)`；
   - 每类一个静态 `PyTypeObject`（`tp_new`/`tp_init`/getset/方法表），类名/`__module__`/`__qualname__` 与源码一致；
   - 局部变量一律 `PyObject*` C 局部；全局以模块 `__dict__` 为唯一真相源，**只读全局**额外缓存进 module state；
   - 异常用 `CRATIX_TRY/CATCH/FINALLY` 宏 + `goto` 清理；`with` 调用 `__enter__/__exit__`；
   - **行号保真**：每函数生成 `static const int32_t fn_lines[]`，运行时维护 thread-local 当前行，异常逃逸时用 `PyException_AddNote()`（3.11+ 公共 API）附 `src/app.py:42`。
5. **函数级回退（O1）**：模块内**不支持的函数**不必让整个模块回退——构建期用标准库 `compile()` 生成该函数的 code object、marshal 后**内嵌进 .pyd 的静态数据**；运行期该函数由 `PyEval_EvalCode` 执行（真 frame、真 traceback、语义 100% 等同源码）。同一模块可同时包含 AOT 函数与解释执行的回退函数。回退粒度：函数级；**类整体回退**（不做方法级混搭）。
6. **分组链接（O4）**：多个模块的 `.c` 可链接进**同一个 `.pyd`**（各自导出唯一 init 符号，天然不冲突）。v1 默认每模块一个 `.pyd`，但构建器从第一天就支持分组（避免上百个 DLL 的 memdl 映射与内存开销）。
7. **驱动**（`compiler/driver.py`）：`.c` → `cl /nologo /O2 /MT /W3 /utf-8 /c /Brepro` → `link /DLL /Brepro python314.lib` → `.pyd`；批量用响应文件（20 文件/批）+ `cl /MP`，批次失败逐文件重试；产物名 `ext/<pkg>_<mod>.pyd`（点转下划线，长度 <300）。
8. **增量缓存**：指纹 = 源 sha256 + 编译器版本 + 解释器身份（`sys.version`/`cache_tag`/`base_prefix`/`opt`）+ 编译选项 + 分组归属。

**v1 受支持子集**（模块级判定，不满足的部分按 O1 逐函数回退）
- 模块级：函数、类、常量赋值、`__all__`、`import`。
- 函数：位置/默认/仅关键字/`*args`/`**kwargs`、局部赋值、算术/比较/布尔/成员/索引/属性/调用、f-string、`if/elif/else`、`for`/`while`、`break/continue`、`try/except/finally`、`with`、`raise`、`assert`、字面量容器、列表/字典/集合推导式、简单 lambda。
- 类：**仅继承 `object`**、实例属性、类属性、普通方法/`classmethod`/`staticmethod`、简单 `property`。
- **不支持（→ 该函数回退）**：`yield`/生成器、`async`/`await`、闭包捕获、装饰器（除恒等）、**继承非 object 基类**、元类、`__slots`、自定义描述符、`dataclass`/`NamedTuple`/`TypedDict`/`Enum`/`Protocol`、运算符重载、`match`、PEP 695 类型别名、`global`/`nonlocal` 写入、`del`、异常组。

**元数据保真清单（O6）**
```
__name__ __qualname__ __module__ __doc__ __defaults__ __kwdefaults__ __annotations__
__code__.co_varnames/co_argcount/co_kwonlyargcount/co_flags/co_name/co_filename
参数名与顺序、仅关键字标记、*args/**kwargs 名字、类 __dict__ 键集合与顺序、
类 __module__/__qualname__（保证 pickle 往返）、__init__ 的参数签名
```
不保证：`inspect.getsource`、可执行字节码、`sys._getframe` 语义（回退函数则有真 frame）。

**差分探针（O3：双子进程 JSON 对比）**
- **两个独立子进程**：进程 A 加载源码版（.pyc），进程 B 加载 AOT 版，各自输出 JSON 观测结果；父进程比对。避免同进程两次加载造成的 `sys.modules` 污染误判（cypack 曾因此产生 58→22→0 次误失败）。
- 比对项：导入成功、模块级名字集合、模块级常量值、函数元数据（O6 清单）、类可实例化/`__dict__`/属性读写/方法调用、错误参数的异常类型名、`pickle` 往返。
- 探针失败 → 该模块（或该函数）回退；`--probe strict` → 构建失败。
- **探针崩溃护栏**：≥90% 结果为同一条错误 → 判定探针崩溃 → 中止构建。
- 探针结论缓存键 = 探针脚本摘要 + 模块集合 + 回退集合 + 解释器指纹 + 数据摘要。

**失败模式与回退矩阵**
| 情况 | 行为 |
| --- | --- |
| 语法错误 / AST 不支持 | 该模块整体回退 .pyc，记录原因 |
| 单个函数超出子集 | 该函数内嵌 code object 回退（O1），模块其余部分仍 AOT |
| 类超出子集 | 整类回退（整模块 `.pyc`） |
| C 编译或链接失败 | 该模块回退 .pyc（strict 则失败） |
| 探针行为不一致 | 回退，记录差异摘要 |
| 探针崩溃 | 中止构建（不降级） |
| MSVC 缺失 | 前置检查直接失败并给安装指引 |

---

## 5. 子系统 C｜引导模块：Python 源码 → 自研编译器 → 原生 C（.pyd）

### 5.1 为什么不用“手写 C 引导模块”
| 路线 | 启动开销 | 可维护性 | 与 cypack stub 契合度 | 结论 |
| --- | --- | --- | --- | --- |
| (a) 手写 C 引导模块 | 最快（~20 ms） | **最差**：meta.json 解析、finder/path-hook 类、resources reader、缓存释放与锁全部要手写 `PyTypeObject`（≈2000–3000 行 C） | 完全一致 | **不采用** |
| (b) 纯 Python 引导模块（`.pyc` 进 zip 根） | 较慢（~80–160 ms） | 最好 | 需改 stub：改为 `PyImport_ImportModule("cratix_bootstrap")` | **兜底路径**（M1） |
| (c) **Python 源码 → 自研 AOT 编译器 → 原生 `.pyd`** | **快（目标 ≤40 ms）** | **好**（Python 单一源码） | **与 stub 完全一致** | **主路径**（M2） |

**决定**：(c) 主路径、(b) 兜底。引导模块源码只有一份（`src/cratix/runtime/cratix_bootstrap.py`），由**我们自己的编译器**编译成 C 与 `.pyd`——同时完成 dogfooding。真正必须留在 C 的部分（memdl 映射、PEP 489 `PyModule_FromDefAndSpec2`/`PyModule_ExecDef`、capsule 指针解析）本就在 stub 里，即 `cratix_loader` 桥。

### 5.2 引导模块源码约束（为落在 v1 子集内）
- **不使用 ABC 继承**：`sys.meta_path`/`sys.path_hooks` 只要求鸭子类型的 `find_spec`/`create_module`/`exec_module`，**不继承 `importlib.abc.*`**。
- **不使用装饰器、闭包、生成器、async、`dataclass`、`contextlib`**；不用类型注解。
- 仅用：`json`/`zipfile`/`os`/`sys`/`types`/`importlib.util`/`msvcrt`/`shutil`/`ctypes`（惰性导入）。
- 编译器子集若临时不支持某个构造，构建期自动降级到 (b) 并在 `report.json` 标注 `bootstrap_backend: "pyc"`。

### 5.3 引导模块职责
- `_AotFinder`：置于 `sys.meta_path` 头部，按 `meta.json` 的 `extensions[]` 加载 `ext/*.pyd`：`cratix_loader.load(entry, init, spec)` → 在 `exec_module` 中设置 `__file__`/`__path__`（**包必须给虚拟 `__path__` 前缀**）→ `cratix_loader.exec_def(module)` 执行模块体（**不得在 `create_module` 里执行**，否则包 `__init__` 自我导入导致无限递归）。
- `_VirtualPycLoader`/`_VirtualZipFinder`/`_VirtualTopFinder`/`_virtual_path_hook`：执行 zip 内 `.pyc`（`__init__.pyc` 优先于模块形式），提供 `importlib.resources` reader/path 与 `pkgutil.iter_modules`。
- 兼容层注入：`sys.frozen=True`、`sys._cratix={kind,version,product,build_hash,exe_path,virtual_root,payload_path,mode}`、`__compiled__` 标记。
- 磁盘形态（onedir）：原生模块与解释器直接由**目录**提供，不写 %LOCALAPPDATA%；onefile 若 `--native-mode=disk`，则释放到 `%LOCALAPPDATA%/cratix/rt/b<crc>_<size>/`（`.cratix_inuse` 字节锁、7 天过期清理、拒绝 junction/symlink/hardlink、CRC 校验）。
- 标准流兜底、`CRATIX_ENTRY`/`CRATIX_CODE`/`CRATIX_LOG`/`CRATIX_DEBUG` 诊断开关。
- **不实现**：mypyc 分组/capsule 路径。

---

## 6. 子系统 D｜stub（C）改造清单（O11：只做枚举内改动）
| # | 改造 | 说明 |
| --- | --- | --- |
| D1 | **载荷来源二选一** | 尾部 cookie `CRTX1` → 内嵌（onefile）；否则 `<exe_dir>/payload.zip` → 旁置（onedir） |
| D2 | `PYTHONPATH` 分支 | 内嵌 → `<exe>`（exe 即 zip，zipimport 直读）；旁置 → `<payload.zip 绝对路径>` |
| D3 | **引导模块双路径** | 优先：载荷含 `runtime/cratix_bootstrap.pyd` → memdl + `PyInit_cratix_bootstrap`；否则 `PyImport_ImportModule("cratix_bootstrap")`（zip 根 `.pyc` 兜底） |
| D4 | 改名 | `cypack_loader` → `cratix_loader` |
| D5 | 解释器来源 | onefile → memdl 内存加载（零落盘）；onedir → 优先 `<exe_dir>/python314.dll` 由 OS 加载，否则回退 memdl |
| D6 | 保留 LZMA 解码器 | `xz/LzmaDec.*`（公有领域）**保留**——`stub.c` 无条件引用 `LzmaDec_*`，剔除会导致链接失败；载荷不产生 ZIP_LZMA 条目，故不影响产物行为 |
| D7 | 保留不动 | memdl/VFS/TLS 修补/CRC 校验/错误弹窗 + `cratix_error.log`/GUI-CONSOLE 子系统切换 |
| D8 | 新增开关 | `CRATIX_MODE=onfile|onedir`、`CRATIX_LOG`、`CRATIX_NATIVE_MODE=mem|disk` |

**变更纪律（O11）**：stub/memdl/vfs/tlsfix 属安全敏感的手工内存代码，**只允许 D1–D8 的枚举改动**；任何 C 改动必须通过回归套件（memdl 映射、TLS 槽位、VFS 路径归一、CRC 校验、GUI 无句柄启动）。

**保留 cypack 已验证的 memdl/VFS/TLS 结论**：`GetModuleHandleExW` 钩子、`RtlAddFunctionTable`（entry 数非字节数）、静态 TLS 指令改写（MASM 保寄存器）、`AddDllDirectory`（**无 W 后缀**）、`FindFirstFileW`/ExW 双钩、CRT `_wstat64i32` 钩、stdlib 条目**不虚拟化**（否则 zipimport 被 FileFinder 顶替）、仅暴露纯 Python 根目录、`python3.dll`→`python314.dll` 稳定 ABI 别名。

---

## 7. 子系统 E｜构建编排 / CLI / 配置 / 报告

**CLI（`python -m cratix`）与退出码约定（O13）**
```
cratix build   [--config cratix.toml] [--target onefile|onedir,both] [--probe on|off|strict]
                [--closure ast|runtime] [--native-mode mem|disk] [--console] [--icon path]
                [--entry mod:attr] [--jobs N] [--no-cache] [--explain]
cratix deps    [--entry mod:attr] [--closure ast|runtime]
cratix compile <module> [--emit-c]
cratix probe   [--module ...]
cratix verify  [--no-console] [--zero-disk] [--exe path]
```
退出码：**0** 成功；**1** 构建失败；**2** 产物运行期校验失败；**3** 用法错误。`--explain` 打印每个模块的判定理由（AOT / 函数级回退 / 模块级回退 + 原因）。

**依赖闭包双模式（O5）**：`--closure ast`（默认，vendor 的 AST 静态分析）；`--closure runtime`（构建期隔离环境真实 import 入口并收集 `sys.modules`，补齐动态依赖；代价是可能有副作用 → 显式开关 + 超时保护）。

**构建阶段（对齐 cypack `cmd_build`）**
0. 前置校验：解释器前缀/site-packages、配置合法、entry 格式、图标、目标目录为普通目录（非 junction/symlink）、构建锁（`build/cratix/.build.lock`）、Job Object、**源码零改动基线（O12：记录构建前 `git status`，构建后比对，差异即失败）**。
1. 依赖闭包 + 边界检查。
2. 收集（3 线程并行）：标准库 `.pyc`、项目 `.pyc`、第三方 `.pyc`+包数据。
3. 原生二进制收集 + PE 导入闭包校验（缺非系统 DLL → 构建失败）。
4. **AOT 编译 + 差分探针**（独立线程，与 2 并行；产出 `ext/*.pyd` + 回退集合；**同时编译引导模块** → `runtime/cratix_bootstrap.pyd`，失败降级为 zip 根 `.pyc`）。
5. 清单构建（`meta.json`）+ 冒烟模块。
6. 载荷写出（zstd/deflate 预压缩缓存）+ stub 编译（并行）。
7. 拼装：onefile → `[stub][zip][cookie32]`；onedir → `<product>.exe` + `payload.zip` + `python314.dll`。

**`meta.json`（兼容 cypack schema + 扩展）**：保留 `product/version/entry/modules/third_party_tops/entry_count/extensions[]/native_deps/profile`；`extensions[]` = `{module, entry, init, kind, virtual, virtual_dir, aot: {functions, fallback_functions}}`，`init` **必须是唯一的 `PyInit_cratix_<hash>`**；新增 `"cratix": {"version","compiler","mode","build_hash","bootstrap":{"entry","init"}}`。**运行期契约带版本号**，stub/引导模块校验不匹配即报错退出。

**`report.json`**：`version/product/exe/exe_bytes/exe_sha256/targets/planned_modules/compiled_modules/fallback_modules/compiler_class{aot,pyc}/aot_modules[]/pyc_modules[]/module_tree[]/compile_failures{}/probe_failures{}/aot{compiled,functions_aot,functions_fallback,failures_by_reason,timings}/timings{}/total_seconds/entry_count/third_party_tops[]/native_deps_missing[]/bootstrap_backend`。

**配置 `cratix.toml`（`tomllib`）**
```toml
[project]
name = "hello"; version = "0.1.0"; entry = "hello.app:main"

[build]
targets = ["onefile", "onedir"]; console = false; icon = "assets/app.ico"
closure = "ast"; native_mode = "mem"

[compiler]
backend = "pyc"; aot = "opt-in"; scope = "project"; packages = []; skip = []
probe = "on"; opt = 2; group_size = 24

[payload]
exclude_modules = []; exclude_stdlib = []; exclude_packages = []; native_in_package = []
```

---

## 8. 里程碑、验收与工时（单人估算）
| 里程碑 | 内容 | 验收标准 | 估时 |
| --- | --- | --- | --- |
| **M0** 骨架 + vendor | 仓库结构、vendor 并按清单改造、CLI `deps/build`、改名 | 示例工程产出 onefile exe 并运行成功；两次构建 sha256 一致；构建链无第三方编译器 | 1 周 |
| **M1** 运行期 + 双形态 | 引导模块 **.pyc 兜底路径**；stub 改造 D1–D8；onedir 目录版语义 | onefile 零落盘；onedir 可用且原生模块从目录加载；`verify --no-console` 通过 | 1.5 周 |
| **M2** AOT 内核 + 引导模块自举 | IR + IR 黄金模型 + C 后端（模块级函数/常量/简单类）+ 函数级回退 + 分组链接 + 探针双进程 | 引导模块由自研编译器产出 `runtime/cratix_bootstrap.pyd` 并被 stub 加载（引导 ≤40 ms）；≥1 个业务模块 AOT 化且探针 0 差异；基准 ≥1.2× | 4 周 |
| **M3** 子集扩展与语义对齐 | 类/控制流/异常/推导式/装饰器/property/元数据保真 | `tests/corpus` 全绿；无静默语义偏差 | 3 周 |
| **M4** 增量 / 缓存 / 可复现 / 报告 | 增量缓存、report.json、可复现性检查表 | 热构建 ≤5 s；连续两次构建 sha256 一致（PYTHONHASHSEED 变化亦然） | 1.5 周 |
| **M5** 性能与覆盖率 | 闭包 cell、slot 化属性、只读全局缓存、常量池、覆盖率提升 | 基准 ≥1.5×；`aot_coverage` 提升至目标线；启动不劣化 >15% | 2–3 周 |
| **M6** 跨平台预留 | §2 四接口抽象 + Linux/macOS 占位 | Windows 实现完全走抽象接口 | 1 周 |

合计 **约 14–16 周**。M2/M3 是主要成本与主要风险来源；M0/M1 交付的“仅 .pyc 打包”本身即是一个可用产品。

**可复现性检查表（O7）**
1. 所有集合遍历 `sorted()`（cypack 实测 `rglob` 顺序有 20 处倒置）；2. zip 条目固定 DOS 时间戳 `(1980,1,1,0,0,0)`；3. `meta.json`/报告 JSON `sort_keys` + 固定分隔符；4. BFS 队列排序（消除 `PYTHONHASHSEED` 影响）；5. `cl` 与 `link` 均加 `/Brepro`；6. 生成代码不含时间戳/绝对路径（`__FILE__` 归一为相对路径）；7. 载荷条目插入顺序稳定（重写键不改变首次插入位）。

---

## 9. 风险登记与对策
| 风险 | 级别 | 对策 |
| --- | --- | --- |
| AOT 与 CPython 语义差异 | 高 | 子集白名单 + 函数级回退 + 双进程差分探针 + IR 黄金模型 |
| 子集覆盖不足 → 性能收益不明显 | 中高 | `backend=pyc` 默认、`aot=opt-in`，按 `aot_coverage` 渐进扩子集 |
| 引导模块无法被自研编译器编译 | 中 | §5.2 源码约束；构建期自动降级 `.pyc` 兜底 |
| traceback 行号不可得 | 中 | `PyException_AddNote` + 行号表；回退函数有真 frame |
| memdl/TLS/VFS 隐性坑 | 中 | 继承已验证实现；C 只做 D1–D8 枚举改动 + 回归套件 |
| MSVC 工具链依赖 | 中 | vswhere 自动定位 + 前置检查 + 明确报错；`/Brepro` |
| vendor 代码与上游分叉 | 中 | `docs/vendor-sources.md` 记录来源与改动 |
| 许可/再分发（zstd、MSVC runtime） | 中 | 保留 zstd LICENSE；删除 LZMA SDK；文档给出责任说明 |
| 解释器升级导致 ABI 失配 | 低 | AOT 缓存指纹含解释器身份；构建期校验版本一致 |
| 第三方原生扩展在 memdl 下崩溃（静态 TLS） | 低-中 | `--native-mode=disk` 逃生通道 + 支持矩阵文档 |

---

## 10. 测试策略
- **单元**：前端（子集判定/作用域/变换）、IR-VM 与源码等价、C 后端与 IR-VM 等价、`pe.py` 边界、`payload.py` 条目命名/压缩策略/缓存、`assemble.py` 校验与原子写。
- **语料**：`tests/corpus/**` 覆盖各类语法（含刻意不支持的构造），逐个跑 “AOT vs 源码” 差分探针；**引导模块自身作为语料之一**。
- **端到端**（`tests/e2e`）：构建 `examples/hello` → 运行 → 退出码 0 → 零落盘复检 → 两次构建一致性 → `verify --no-console` → **两种引导路径（原生 .pyd / .pyc 兜底）分别验证**。
- **回归移植**：vendor cypack 既有回归脚本（meta_audit / payload_deep_audit / deflatecache / zstd_manifest / handoff / crc16）到 `tests/`。
- **性能基准**：`tests/bench/` 固定脚本，AOT vs .pyc 对照，连续 20 次取中位数；启动分段计时。

---

## 11. 性能与启动预算（O10）
| 阶段 | 目标（参照 cypack 实测） |
| --- | --- |
| 依赖闭包（AST） | ≤2 s（1000 文件级项目） |
| AOT 编译（100 模块，`cl /MP` + 分组） | ≤60 s（冷）· ≤5 s（增量） |
| 载荷写出（缓存命中） | ≤10 s |
| **构建热态总计** | **≤5 s**（M4 验收线） |
| 启动：读 exe + 解压解释器 + OS 加载 + `Py_Initialize` | ≤150 ms |
| 启动：加载桥 + **引导模块** | **≤40 ms**（原生路径）· ≤160 ms（.pyc 兜底） |
| 启动：入口 import | 应用侧决定，打包侧 ≤100 ms |

---

## 12. v1 明确不做（留待后续）

生成器/async 的 AOT 化、元类/描述符/dataclass 的 AOT 化、字节码级混淆与符号保护、Qt/PySide6 支持、跨平台原生加载器（ELF/Mach-O）、自举（用 AOT 编译 cratix 自身）、手写 C 引导模块。