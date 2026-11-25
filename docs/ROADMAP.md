# cratix 路线图

## 进度

| 里程碑 | 交付物 | 验收线 | 估时 | 状态 |
| --- | --- | --- | --- | --- |
| M0 骨架 + vendor | 仓库结构、vendor 打包半边并去业务化、统一改名、CLI deps/build | 示例工程产出 onefile exe 并运行成功；两次构建 sha256 一致；构建链无第三方编译器 | 1 周 | ✅ 完成 |
| M1 运行期 + 双形态 | 纯 Python 引导模块(.pyc 兜底)、stub 改造 D1–D8、onedir 目录版 | onefile 零落盘；onedir 可用；verify --no-console 通过 | 1.5 周 | ✅ 完成（D3/D9/D1/D2 已落地 ✅） |
| M2 AOT 内核 + 自举 | IR + 黄金模型 + C 后端 + 函数级回退 + 分组链接 + 双进程探针 | 引导模块由自研编译器产出 .pyd 并被 stub 加载(≤40ms)；≥1 业务模块 AOT 化且探针 0 差异 | 4 周 | ⬜ 未开始 |
| M3 子集扩展与语义对齐 | 类/控制流/异常/推导式/装饰器/property/元数据保真 | tests/corpus 全绿；无静默语义偏差 | 3 周 | ⬜ 未开始 |
| M4 增量/缓存/可复现/报告 | 增量缓存、report.json、可复现性检查表 | 热构建 ≤5s；两次构建 sha256 一致 | 1.5 周 | 🟡 report.json 与可复现性已达成；增量缓存未做 |
| M5 性能与覆盖率 | 闭包 cell、slot 化属性、全局缓存、常量池、覆盖率提升 | 基准 ≥1.5×；启动不劣化 >15% | 2–3 周 | ⬜ 未开始 |
| M6 跨平台预留 | 四接口抽象 + Linux/macOS 占位 | Windows 实现完全走抽象接口 | 1 周 | ⬜ 未开始 |

## M0/M1 已完成的事实

- **onefile**：15.5 MB 单 exe，600 个载荷条目，24 个标准库 C 扩展，3 个原生条目；
- **onedir**：`hello_0.1.0/hello.exe`（397 KB）+ `payload.zip`（15.1 MB）；
- 启动耗时：onefile 313 ms / onedir 250 ms（读载荷 + CRC 16 ms → 内存映射解释器 31 ms → `Py_InitializeEx` 47 ms → 引导 125 ms → 入口 16 ms）；
- 运行期零落盘（TEMP 独立目录 before/after 无新增），onefile 与 onedir 均通过；
- 无标准句柄启动（= Explorer 双击）退出码 0；
- 连续两次构建 sha256 一致；
- **21 项测试全绿**（15 单元 + 6 端到端）；
- 构建链只依赖 CPython + MSVC，无任何第三方编译器。

### M1 期间修掉的真实缺陷

| 缺陷 | 根因 | 修法 |
| --- | --- | --- |
| `vendor_sync` 冲掉 stub 本地改动 | 同步脚本直接覆盖 `stub.c` | `stub.c` 改为 cratix 自有 ✅ + 同步后**标记校验** ✅ |
| 构建误判"非系统 DLL" | 上游系统 DLL 白名单漏 `comctl32.dll` | `sysdll.py`：白名单 ∪ **System32 实际存在** ✅ |
| 产物替换被拒（WinError 5） | 杀毒软件短暂占用 exe | 编排层**有界重试** ✅（`pipeline._retry_on_lock`） |
| onedir 引导失败（BadZipFile） | 引导模块硬读 `sys.executable` | stub 导出 `CRATIX_PAYLOAD` ✅ → 引导按它读 ✅ |
| 解释器条目写死 python314 | stub 硬编码版本 | `find_interpreter_entry()` 版本无关扫描 ✅ |

## 下一步（M2）

1. AOT 内核：前端（scope/subset/rewrite）→ 自有 IR → 黄金模型（IR 解释器）→ C 后端；
2. 函数级回退：不支持的函数内嵌 code object，运行期由 `PyEval_EvalCode` 执行；
3. 双进程差分探针；
4. 引导模块原生化：自研编译器产出 `runtime/cratix_bootstrap.pyd`（stub 已支持该路径 ✅）。

详细设计见 [PLAN.md](PLAN.md)。