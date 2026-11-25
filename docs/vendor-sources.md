# vendor 来源记录

本仓库的部分代码来自 `C:/project/sports-server/tools/cypack`（原作者仓库，按实际归属保留）。

**同步方式**：`python scripts/vendor_sync.py` —— 幂等 ✅；改名与去业务化改写集中在该脚本 ✅；
被排除的文件保留本地版本 ✅（见下）。

| 目标文件 | 来源 | 本地改动 |
| --- | --- | --- |
| `src/cratix/packaging/deps.py` | `tools/cypack/deps.py` | 缓存路径参数化 |
| `src/cratix/packaging/pe.py` | `tools/cypack/pe.py` | 无（仅路径） |
| `src/cratix/packaging/payload.py` | `tools/cypack/payload.py` | 裁剪表改配置驱动（`defaults.`）、cookie/loader 改名、业务 `PROJECT_TOPS` 移除 |
| `src/cratix/packaging/assemble.py` | `tools/cypack/assemble.py` | cookie magic `CRTX1` |
| `src/cratix/packaging/stubbuild.py` | `tools/cypack/stubbuild.py` | stub 源目录改指 `src/cratix/stub` |
| `src/cratix/packaging/manifest.py` | `tools/cypack/manifest.py` | 无（仅路径） |
| `src/cratix/packaging/native.py` | `tools/cypack/native.py` | 裁剪表改配置驱动 |
| `src/cratix/packaging/upstream_trims.py` | `tools/cypack/trims.py` | 改名保留（仅取**通用安全表**：系统 DLL 白名单 / 稳定 ABI 别名） |
| `src/cratix/stub/**`（除 stub.c） | `tools/cypack/stub/**` | 改名（`cypack`→`cratix`） |
| `src/cratix/stub/stub.c` | `tools/cypack/stub/stub.c` | **cratix 自有**（见下） |

## cratix 自有的文件

| 文件 | 说明 |
| --- | --- |
| `src/cratix/stub/stub.c` | **不随上游覆盖** ✅。改造点：D3 引导模块双路径（原生 `.pyd` 优先 / zip 根 `.pyc` 兜底）、D9 解释器条目版本无关查找（`native/python3*.dll`）。上游变更需手工合并。 |
| `src/cratix/packaging/defaults.py` | 裁剪表配置驱动（`cratix.toml` 的 `[payload]` 段）；通用安全表从 `upstream_trims.py` 取。 |
| `src/cratix/packaging/sysdll.py` | 系统 DLL 判定：**上游白名单 ∪ System32 实际存在** ✅（上游白名单漏 `comctl32.dll` ✅ → 构建失败 ✅）。 |
| `src/cratix/packaging/verify.py` | 由上游 `verify_no_console.py` + `verify_zero_disk.py` 重写为**可导入函数** ✅（上游是独立脚本 ✅）。保留其安全护栏 ✅（临时目录守卫 ✅ / 环境消毒 ✅ / 相对路径比较 ✅）。 |
| `src/cratix/build/**`、`src/cratix/cli.py`、`src/cratix/config.py`、`src/cratix/runtime/**` | cratix 自研 ✅。 |

## 保留的 cypack 不变量（改动时勿破坏）

1. 仓库源码零改动（改写只发生在暂存副本）；
2. 每个包的 `__init__.pyc` 必须进 zip；
3. 回退清单在探针前算好；
4. zstd 缓存复用前提是 `/Brepro`；
5. `.pyc` 用 DEFLATE、二进制走预压缩 zstd 直通；
6. 构建结束输出总耗时 + 各阶段耗时 + 逐模块分类；
7. 构建目录非普通目录（符号链接/目录联接）即拒绝；
8. 所有构建产物原子写（临时文件 + `os.replace`）。