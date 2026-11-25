# cratix

自研 Python 打包工具：**AOT 编译器（自研）** + **单文件 / 目录发行版打包**。

设计要点：

- **编译部分自研**：把 Python 模块编译成原生扩展（CPython C-API 后端），**不依赖 Cython / mypyc / mypy**；
- **打包部分复用已验证实现**：vendor 自 `tools/cypack`（内存加载 stub + 载荷容器 + 可复现构建），已去业务化；
- **运行期零落盘**：onefile 形态把解释器 / 标准库 / 项目代码全部放进 exe 内存，运行期不写磁盘；
- **可复现**：连续两次构建产物 sha256 一致。

完整设计与里程碑见 [docs/PLAN.md](docs/PLAN.md) 与 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

## 现状（2026-11-25）

| 里程碑 | 状态 |
| --- | --- |
| M0 骨架 + vendor | ✅ 完成 |
| M1 运行期 + 双形态 | ✅ 完成（onefile + onedir 双形态 + 零落盘 + 可复现） |
| M2 AOT 内核 | ✅ 完成（自研编译器产出 .pyd，已接入打包链，运行期从内存加载 ✅） |
| M3 子集扩展 | ✅ **完成**（含 `from … import` ✅ / `global` ✅ / 非局部复合赋值 ✅；自测语料 AOT 覆盖率 **92.9%**）（类 / try-except / **try-else · try-finally ✅** / with / 循环 else / 装饰器 / property / 推导式（含多生成器·解包·嵌套）/ 生成器表达式（真惰性）/ 元数据保真） |
| M4 增量/可复现 | ✅ 完成（指纹增量 + report.json + 可复现） |
| M5 性能 | 🟡 全部用例几何平均 1.13×（add 1.39×）；AOT 覆盖率报告 + strict-aot 严格模式 |
| M6 跨平台 | 🟡 **四接口抽象 ✅ + Linux/macOS 占位 ✅**（Windows 全走接口 ✅，真实移植未做） |

**当前可用能力**：`onefile` 单文件 exe 与 `onedir` 目录发行版构建 + 运行 + 零落盘 + 字节级可复现；
`--backend aot` 用**自研编译器**把项目模块编成原生扩展并在运行期从内存加载 ✅；
AOT **增量编译** ✅（指纹命中即跳过 cl+link ✅，热构建 3.3 s ✅）；
性能基准 ✅（`tests/bench/bench_aot.py` ✅，**每例实跑一批** ✅ + 几何平均 ✅）——
`add 1.43×` ✅ / `control 1.05×` ✅ / `strings 0.99×` ✅ / `containers 1.00×` ✅ / `mixed 0.99×` ✅，
**corpus_basic 几何平均 1.20×** ✅、全部用例几何平均 **1.25×** ✅（诚实记录 ✅）；
**质量门** ✅：`python scripts/check.py` 一次跑完 测试 + 严格 AOT 构建 + 零落盘冒烟 + 基准 ✅；
**基准历史** ✅：`bench_aot.py --record/--history` ✅；
**星号解包** ✅（`a, *b = seq` ✅）：`containers 0.99× → 1.14×` ✅；
**内建方法直译** ✅（`list.append` / `dict.get` ✅）：`control 1.03× → 1.32×` ✅、`loop_str 1.50× → 1.59×` ✅；
**原生方法描述符** ✅（去掉 Python 跳板帧 ✅）+ **实例结构体/属性槽位** ✅（按偏移直接读写 ✅）：
`attribute_reads 0.41× → 1.07×` ✅、`self_calls 0.85× → 1.08×` ✅、`methods 0.39× → 1.09×` ✅；
**常量池** ✅（字面量只建一次 ✅）+ **热路径去分配** ✅（方法名/全局键/f-string 常量 ✅）+
**vectorcall 借用语义修正** ✅（调用参数不再泄漏引用 ✅，`control` 0.56→1.05× ✅）；
**长循环不泄漏** ✅（临时变量按作用域每轮释放 ✅，`sys.getrefcount` 差值为 0 ✅）；
**`with` 语句** ✅ —— 上下文管理器走**统一帧栈** ✅（与 try 共用 ✅）：
`__enter__`/`__exit__` 全路径 ✅（正常 / 异常 / 抑制 / return / break / continue）+ 多管理器嵌套 ✅；
**`for ... else` / `while ... else`** ✅ —— 按 CPython 语义（break 不跑 else）✅。

---

## 快速开始

```powershell
# 环境：CPython 3.14（需含 include/ 与 libs/）+ MSVC（vswhere 可定位）

# 构建示例工程
$env:PYTHONPATH = "src"
python -m cratix build --config examples/hello/cratix.toml

# 从零开始 ✅
python -m cratix init myapp --name myapp --package myapp
cd myapp && python -m cratix build

# 运行产物
./examples/hello/build/public/hello_0.1.0.exe

# 验收（零落盘 / 无标准句柄启动 / 冒烟）
python -m cratix verify --config examples/hello/cratix.toml --zero-disk
python -m cratix verify --config examples/hello/cratix.toml --no-console

# 测试
python -m unittest discover -s tests -p "test_*.py"

# 性能基准 ✅
python tests/bench/bench_aot.py --repeat 5
```

---

## 命令

```
cratix build   [--config cratix.toml] [--target onefile|onedir] [--entry mod:attr]
                [--console|--no-console] [--icon path] [--backend pyc|aot] [--probe on|off|strict]
               [--console|--no-console] [--icon path] [--backend pyc|aot] [--probe on|off|strict]
cratix deps    [--config cratix.toml] [--entry mod:attr]
cratix verify  [--config cratix.toml] [--exe path] [--zero-disk] [--no-console]
cratix init    <目录> [--name 名称] [--package 包名]      # 生成项目脚手架 ✅
cratix info    [--config cratix.toml]                    # 显示生效配置 ✅
```

退出码：`0` 成功 · `1` 构建失败 · `2` 产物运行期校验失败 · `3` 用法错误。

---

## 配置（cratix.toml）

```toml
[project]
name = "hello"
version = "0.1.0"
entry = "hello.app:main"

[build]
targets = ["onefile"]
console = true

[compiler]
backend = "pyc"     # pyc = 只打包字节码；aot = 走自研 AOT 编译器（M2）
probe = "on"

[payload]
exclude_modules = []
exclude_stdlib = []
exclude_packages = []
native_in_package = []   # 覆盖默认的（python3xx.dll / python3.dll / zlib1.dll）
```

---

## 目录结构

```
src/cratix/
  cli.py              命令行
  config.py           cratix.toml 读取与校验
  paths.py            路径常量
  build/              构建编排 + report.json
  compiler/           自研 AOT 编译器（M2 起）
  packaging/          打包半边（vendor 自 cypack + cratix 自有增强）
    deps.py  pe.py  payload.py  assemble.py  stubbuild.py   # vendor
    defaults.py  sysdll.py  verify.py                       # cratix 自有
  platforms/          平台接口（M6 ✅）：base 接口 + windows 实现 + linux/darwin 占位
  runtime/            随包运行的引导模块（单一 Python 源码）
  stub/               启动器 C 源码（memdl / VFS / TLS 修补 / zip 读取）
scripts/vendor_sync.py  vendor 同步（改名 + 去业务化改写，幂等）
tests/                 单元 + 端到端（只用标准库 unittest）
examples/hello/        示例工程
```

---

## 设计约束（改动时勿破坏）

1. **仓库源码零改动** —— 构建期所有改写只发生在暂存副本；
2. **每个包的 `__init__.pyc` 必须进 zip** —— 否则子模块不可达；
3. **缺非系统 DLL 依赖即构建失败** —— 不是警告；
4. **`.pyc` 用 DEFLATE、二进制走预压缩 zstd** —— `zipfile` 只认 STORED/DEFLATE；
5. **可复现**：集合遍历一律 `sorted()`、zip 条目固定时间戳、`cl`/`link` 均加 `/Brepro`；
6. **stub 的 C 代码只做枚举内改动** —— memdl/VFS/TLS 属安全敏感的手工内存代码；
7. **`stub/stub.c` 是 cratix 自有文件** —— `vendor_sync` 不覆盖它，并用标记校验防止改动被冲掉。