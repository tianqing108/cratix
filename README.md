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
| M3–M6 | ⬜ 未开始 |

**当前可用能力**：`onefile` 单文件 exe 与 `onedir` 目录发行版构建 + 运行 + 零落盘 + 字节级可复现；
`--backend aot` 用**自研编译器**把项目模块编成原生扩展并在运行期从内存加载 ✅；
AOT **增量编译** ✅（指纹命中即跳过 cl+link ✅，热构建 3.3 s ✅）。

---

## 快速开始

```powershell
# 环境：CPython 3.14（需含 include/ 与 libs/）+ MSVC（vswhere 可定位）

# 构建示例工程
$env:PYTHONPATH = "src"
python -m cratix build --config examples/hello/cratix.toml

# 运行产物
./examples/hello/build/public/hello_0.1.0.exe

# 验收（零落盘 / 无标准句柄启动 / 冒烟）
python -m cratix verify --config examples/hello/cratix.toml --zero-disk
python -m cratix verify --config examples/hello/cratix.toml --no-console

# 测试
python -m unittest discover -s tests -p "test_*.py"
```

---

## 命令

```
cratix build   [--config cratix.toml] [--target onefile|onedir] [--entry mod:attr]
               [--console|--no-console] [--icon path] [--backend pyc|aot] [--probe on|off|strict]
cratix deps    [--config cratix.toml] [--entry mod:attr]
cratix verify  [--config cratix.toml] [--exe path] [--zero-disk] [--no-console]
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