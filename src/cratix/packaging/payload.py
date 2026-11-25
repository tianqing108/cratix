"""归档构建：把标准库、项目代码、原生二进制打成 exe 尾部的 zip 载荷。

约束：
    - **除 zlib1.dll 外全部走 DEFLATE** ✅（stub 用 zlib1.dll 解 ✅；Python 侧 zipfile/zipimport 都能读 ✅）；
    - 条目路径用 POSIX 分隔符，且与源码同构（client/…、common/…、encodings/…）。
"""

from __future__ import annotations

import collections
import contextlib
import hashlib
import multiprocessing
import os
import sys
import time

import compileall
import json
import py_compile
import shutil
import zipfile
from pathlib import Path

from . import defaults, pe

# 标准库要排除的子目录（服务端/开发用）
STDLIB_EXCLUDE_DIRS = {
    "site-packages",
    "test",
    "tests",
    "idlelib",
    "tkinter",
    "ensurepip",
    "lib2to3",
    "__pycache__",
    "turtledemo",
    "config",
    "libdynload",
}


def compile_pyc(source: Path, dest: Path, dfile: str | None = None) -> bool:
    """把 .py 编成「未校验哈希」的 .pyc（zipimport 只看 magic，不依赖源文件 mtime）。

    同时按「源码内容 + dfile + Python magic」缓存：源码未变直接复用 pyc。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    import hashlib
    import importlib.util as _ilu

    sidecar = dest.with_name(dest.name + ".sha")
    try:
        key = "|".join(
            (
                hashlib.sha1(source.read_bytes()).hexdigest(),
                dfile or str(source),
                sys.version,
                _ilu.MAGIC_NUMBER.hex(),
            )
        )
        if (
            dest.is_file()
            and not dest.is_symlink()
            and dest.stat().st_nlink == 1
            and sidecar.read_text(encoding="utf-8") == key
            and _pyc_valid(dest.read_bytes())
        ):
            return True
    except OSError, ValueError:
        pass
    clear_leaf(dest)
    try:
        py_compile.compile(
            str(source),
            cfile=str(dest),
            dfile=dfile or str(source),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )
        try:
            _write_atomic(sidecar, key.encode("utf-8"))
        except OSError:
            pass
        return True
    except py_compile.PyCompileError, OSError, ValueError:
        return False


def _prune_old_files(directory: Path, days: int = 30) -> None:
    """清理陈旧缓存文件 ✅（按 mtime 窗口 ✅）。
    用于 stage/pyc、stage/pyc3rd —— 源码模块删除/包升级后 ✅ 旧 .pyc/.sha 不再被用 ✅
    但一直堆着 ✅（旧实现无任何清理 ✅）。"""
    try:
        cutoff = time.time() - days * 86400
        if not directory.is_dir():
            return

        # **目录连接不跟随** ✅：构建目录里被植入 junction ✅
        # → rglob/rmdir 可能动到外部目标 ✅（与 A203/A204/A206 同类 ✅）。
        def _is_link(p: Path) -> bool:
            return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())

        # **用 os.walk 并剪掉链接目录** ✅：Path.rglob 会跟穿 junction ✅
        # → 列出目标里的文件 ✅ → 被本函数当成构建缓存删掉 ✅（实测 ✅）。
        for root, dirs, files in os.walk(directory, topdown=True, followlinks=False):
            dirs[:] = [d for d in dirs if not _is_link(Path(root) / d)]
            for name in files:
                p = Path(root) / name
                try:
                    if _is_link(p):
                        continue
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                except OSError:
                    pass
        # **清掉空目录** ✅：文件删后留下的空目录也占盘 ✅
        for root, dirs, _files in os.walk(directory, topdown=False, followlinks=False):
            for name in dirs:
                d = Path(root) / name
                try:
                    if _is_link(d):
                        continue
                    if not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass
    except OSError:
        pass


def _collect_stdlib_zip(zpath: Path, log=print) -> dict[str, bytes]:
    """从 embeddable 的 pythonXY.zip 收集标准库 .pyc（内层 zip 是 .pyc，无 .py）。

    条目名相对 zip 根；按 XML 顺序排序保证可复现。
    """
    import io
    import zipfile

    entries: dict[str, bytes] = {}

    def _harvest(zf: zipfile.ZipFile, prefix: str = "") -> None:
        for name in sorted(zf.namelist()):
            if name.endswith("/"):
                continue
            if name.lower().endswith(".zip"):
                # **embeddable 的 pythonXY.zip 是嵌套的**：递归一层即可。
                _harvest(zipfile.ZipFile(io.BytesIO(zf.read(name))), prefix)
                continue
            if not name.endswith(".pyc"):
                continue
            rel = name[len("Lib/") :] if name.startswith("Lib/") else name
            top = rel.split("/")[0]
            if top in STDLIB_EXCLUDE_DIRS or top in defaults.EXCLUDED_PACKAGES or top in defaults.EXCLUDED_STDLIB_MODULES:
                continue
            blob = zf.read(name)
            if not _pyc_valid(blob):
                raise SystemExit(f"[payload] 标准库 zip 中的 .pyc 损坏/版本不符：{zpath}!{name}")
            entries[rel] = blob

    with zipfile.ZipFile(zpath) as outer:
        _harvest(outer)
    log(f"[payload] 标准库（zip {zpath.name}）条目：{len(entries)}")
    return entries


def collect_stdlib(python_prefix: Path, stage: Path, log=print) -> dict[str, bytes]:
    """把标准库编译成 .pyc 并收集为 {条目名: 字节}（条目名相对 zip 根）。"""
    lib = python_prefix / "Lib"
    if not lib.is_dir():
        # **embeddable 发行版没有 Lib**：标准库在 pythonXY.zip（内层是 .pyc）。
        zips = sorted(p for p in python_prefix.glob("python*.zip") if p.is_file())
        if zips:
            return _collect_stdlib_zip(zips[0], log)
        raise FileNotFoundError(f"找不到标准库目录：{lib}")
    stage.mkdir(parents=True, exist_ok=True)
    src_dir = stage / "stdlib_src"
    # **stdlib_src 不能是链接/junction** ✅：下面会 rmtree ✅
    # → 否则会删到链接目标 ✅（A203/A204/A206/A207/A208 同类 ✅）。
    if src_dir.is_symlink() or (hasattr(src_dir, "is_junction") and src_dir.is_junction()):
        raise SystemExit(f"[payload] 拒绝：stage/stdlib_src 是符号链接/目录连接：{src_dir}")
    marker = src_dir / ".done"
    # **标记带解释器指纹** ✅：只判「存在」会在**解释器升级**（.venv 换 3.14.x 补丁版 ✅）后
    # 继续用旧拷贝 ✅ → 旧 magic 的 .pyc → 运行期 zipimport 拒载 ✅（陈旧构建 ✅）。
    # **剪裁配置也进标记** ✅：STDLIB_EXCLUDE_DIRS 变了 ✅ → 旧剪裁树不能复用 ✅
    # （否则新增的排除目录仍在缓存里 ✅ → 仍会被收进载荷 ✅）。
    want_marker = (
        f"{sys.version}|{sys.base_prefix}|opt={sys.flags.optimize}|pycpipe={PYC_PIPELINE_VERSION}|"
        # **源码前缀也要进标记** ✅：旧口径只有构建解释器的 base_prefix ✅ ——
        # CRATIX_PYTHON_HOME 换到同版本的另一个安装（自定义/embeddable ✅）时标记不变 ✅
        # → 旧 stdlib_src 被复用 ✅ → 载荷里是**上一个前缀**的标准库 ✅（实测 aaa→aaa）。
        + f"lib={lib.resolve()}|"
        + "excl="
        + ",".join(sorted(STDLIB_EXCLUDE_DIRS))
    )
    _marker_ok = False
    try:
        _marker_ok = marker.is_file() and marker.read_text(encoding="utf-8", errors="replace") == want_marker
    except OSError:
        _marker_ok = False
    if not _marker_ok:
        # 复制一份再编译：绝不在系统安装目录里写 __pycache__
        shutil.rmtree(src_dir, ignore_errors=True)
        log("[payload] 复制标准库到暂存目录 ...")
        shutil.copytree(lib, src_dir, ignore=shutil.ignore_patterns(*STDLIB_EXCLUDE_DIRS))
        log("[payload] 编译标准库 ...")
        # **优化级别必须与标记一致**：旧写法硬编码 optimize=0，
        # 而 marker 记录 sys.flags.optimize → -O 构建里标准库仍带 assert，
        # 与项目/第三方 pyc（已是 -O）不一致，且下次普通构建会无谓重编。
        ok = compileall.compile_dir(
            str(src_dir),
            quiet=1,
            force=True,
            legacy=False,
            optimize=sys.flags.optimize,
            workers=4,
        )
        if not ok:
            # **编译失败不能标记成功** ✅：旧实现忽略返回值 ✅ →
            # 下次直接复用残缺的标准库树 ✅ → 载荷静默缺模块 ✅。
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            raise SystemExit("[payload] 标准库编译失败（compileall 返回 False）—— 检查 Python 安装/磁盘")
        _write_atomic(marker, want_marker.encode("utf-8"))
    entries: dict[str, bytes] = {}
    # **排序** ✅：rglob 的返回顺序是「文件系统目录枚举顺序」✅ 不是字典序 ✅
    # （实测 528 个标准库条目：20 处逆序 ✅）→ entries 的插入顺序会影响 zip 条目顺序 ✅
    # → 换一台机器 / 拷一份源码树 / 换文件系统后**载荷字节变** ✅ → 破坏可复现性 ✅
    # （docs 的口径是「改动收集顺序类代码时必须重验」✅ —— 这里就是收集顺序类代码 ✅）。
    for path in sorted(src_dir.rglob("*.pyc")):
        rel = path.relative_to(src_dir)
        parts = list(rel.parts)
        if parts[-2:-1] == ["__pycache__"]:
            stem = parts[-1].split(".")[0]
            parts = parts[:-2] + [stem + ".pyc"]
        else:
            continue
        name = "/".join(parts)
        if name.split("/")[0] in defaults.EXCLUDED_PACKAGES:
            continue
        if name.split("/")[0] in defaults.EXCLUDED_STDLIB_MODULES:
            continue  # 用不到的标准库模块 ✅（省体积 ✅）
        # **缓存 .pyc 校验** ✅：旧口径只看 .done 标记 ✅
        # → 个别 .pyc 被截断/写坏后仍进包 ✅（zipimport 拒载 ✅）。
        try:
            blob = path.read_bytes()
        except OSError as exc:
            marker.unlink(missing_ok=True)
            raise SystemExit(f"[payload] 标准库 .pyc 读取失败：{name}（{exc}）") from exc
        import importlib.util as _ilu

        if not _pyc_valid(blob):
            marker.unlink(missing_ok=True)
            raise SystemExit(f"[payload] 标准库 .pyc 缓存损坏：{name}（已清标记，重跑构建会重编）")
        entries[name] = blob
    log(f"[payload] 标准库条目：{len(entries)}")
    return entries


def collect_stdlib_extensions(python_prefix: Path, log=print) -> dict:
    """收集标准库的 **C 扩展模块**（Windows 上是 {prefix}\\DLLs\\*.pyd ✅）。

    标准库不是纯 Python ✅ —— _socket / select / _ctypes / _sqlite3 / _wmi 等是 C 扩展 ✅
    → 不收进来 ✅ → 运行期 import 直接 ModuleNotFoundError ✅
    （实测：真实入口 client.app:main 卡在 `_socket` ✅ —— socket 是 loguru 的依赖 ✅）。

    这些 .pyd 走**同一个内存加载桥** ✅（meta_path finder ✅）→ 与项目 Cython 产物同口径 ✅
    （memdl 映射 ✅ + 唯一 init 符号 ✅）。
    """
    entries: dict[str, bytes] = {}
    extensions: list[dict] = []
    dlls = python_prefix / "DLLs"
    if not dlls.is_dir():
        # **embeddable / 自定义前缀** ✅：.pyd 与 python314.dll 同在前缀根 ✅
        # → 旧口径直接返回 0 个扩展 ✅ → 产物缺 _socket/_ssl ✅ → 运行期 ImportError。
        if any(python_prefix.glob("*.pyd")):
            dlls = python_prefix
            log(f"[payload] 没有 DLLs 目录 → 回退到前缀根收集标准库扩展：{python_prefix}")
        else:
            log("[payload] 没有 DLLs 目录 → 标准库扩展模块 0 个 ⚠️")
            return {"entries": entries, "extensions": extensions}
    for path in sorted(dlls.glob("*.pyd")):
        name = path.stem.split(".")[0]  # 版本标签要剥掉 ✅：foo.cp314-win_amd64.pyd → foo ✅
        if not name or name.lower().startswith("_test"):
            continue
        if name in defaults.EXCLUDED_STDLIB_EXTENSIONS:
            continue  # _tkinter ✅ → 顺带不带 Tcl/Tk DLL ✅
        _blob = path.read_bytes()
        # **标准库扩展也必须是 PE（MZ）** ✅：截断/占位 .pyd ✅
        # 进包后加载不了 ✅ → 直接构建失败 ✅。
        if len(_blob) < 2 or not _blob.startswith(b"MZ"):
            raise SystemExit(f"[payload] 标准库扩展非 PE（构建失败）：{path}")
        entry = f"extstd/{name}.pyd"
        entries[entry] = _blob
        extensions.append(
            {
                "module": name,
                "entry": entry,
                "init": f"PyInit_{name}",
                "kind": "module",
                "virtual": f"<exe>/Lib/{name}.pyd",
            }
        )
    log(f"[payload] 标准库扩展模块：{len(extensions)}")
    return {"entries": entries, "extensions": extensions}


def collect_project(closure, root: Path, stage: Path, log=print) -> dict[str, bytes]:
    """项目模块编译为 .pyc（条目与源码同构）。"""
    _require_plain_dir(stage / "pyc", "stage/pyc")
    stage.mkdir(parents=True, exist_ok=True)
    entries: dict[str, bytes] = {}
    failed: list[str] = []
    for rel in sorted(closure.files):
        src = root / rel
        # **项目 .PY 大写也要认** ✅：Windows 扩展名不区分大小写 ✅
        # → 旧口径直接跳过 ✅ → 回退集缺模块 ✅。
        if src.suffix.lower() != ".py":
            continue
        name = rel[:-3] + ".pyc" if rel.lower().endswith(".py") else rel
        dest = stage / "pyc" / name
        virtual = f"<cratix>/{rel}"
        # **先断链接再编译** ✅：compile_pyc 内部在缓存未命中时 clear_leaf。
        if compile_pyc(src, dest, dfile=virtual):
            # **项目 .pyc 也验证 magic** ✅：编译器/磁盘异常 ✅
            # → 坏字节码进包 ✅。
            try:
                import importlib.util as _ilu

                _blob = dest.read_bytes()
                if not _pyc_valid(_blob):
                    raise ValueError("bad pyc")
            except OSError, ValueError:
                failed.append(rel)
                continue
            entries[name] = _blob
        else:
            # **项目 .py 编译失败必须构建失败** ✅：旧行为只 log 跳过 ✅
            # → 产物静默缺模块 ✅ → 启动时 ModuleNotFoundError ✅。
            failed.append(rel)
    if failed:
        raise SystemExit("[payload] 项目 .py 编译失败（构建失败）：" + ", ".join(failed[:12]))
    log(f"[payload] 项目条目：{len(entries)}")
    return entries


# 可以安全地从**构建解释器**补齐的兼容运行库（ABI 稳定、与 Python 版本无关）。
_COMPAT_DLLS: frozenset[str] = frozenset(
    {
        "zlib1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    }
)


def collect_native(site_packages: Path, python_prefix: Path, wanted: list[str], log=print) -> dict[str, bytes]:
    """收集要进包的原生二进制。"""
    entries: dict[str, bytes] = {}
    missing: list[str] = []
    for item in wanted:
        rel = item.replace("\\", "/")
        candidates = [
            site_packages / rel,
            python_prefix / rel,
            site_packages / "PySide6" / Path(item).name,
            site_packages / "shiboken6" / Path(item).name,
            python_prefix / "DLLs" / Path(item).name,
        ]
        # **embeddable 前缀缺构建期兼容 DLL**（zlib1/msvcp/vcruntime）：
        # 只对白名单做「构建解释器前缀」兜底；python314.dll 等绝不做（避免双解释器/版本错配）。
        if Path(item).name.lower() in _COMPAT_DLLS:
            candidates += [
                Path(sys.base_prefix) / "DLLs" / Path(item).name,
                Path(sys.base_prefix) / Path(item).name,
            ]
        for cand in candidates:
            # **0 字节原生文件不算命中** ✅：截断/占位文件进包 ✅
            # → 运行期 LoadLibrary 失败 ✅；当作缺失走 A107 失败路径 ✅。
            if cand.is_file() and cand.stat().st_size > 0:
                _blob = cand.read_bytes()
                # **原生文件必须是 PE（MZ）** ✅：0 字节之外的截断/占位文件 ✅
                # 进包后仍然加载不了 ✅ → 当作缺失走 A107 失败路径 ✅。
                if not _blob.startswith(b"MZ"):
                    continue
                entries["native/" + item.replace("\\", "/")] = _blob
                break
        else:
            # **必需的原生文件缺失 → 构建失败** ✅：旧行为只打印 ✅
            # → 产物启动时才发现缺 python314/Qt DLL ✅。
            missing.append(item)
    if missing:
        raise SystemExit("[payload] 原生文件缺失（构建失败）：" + ", ".join(missing[:12]))
    log(f"[payload] 原生条目：{len(entries)}")
    return entries


def native_dependencies(entries: dict[str, bytes], site_packages: Path, log=print) -> dict[str, list[str]]:
    """用 PE 导入表推导原生文件的 DLL 依赖（用于校验清单是否完整）。

    **从内存字节解析** ✅：blob 本来就在 entries 里 ✅ —— 旧口径逐个写临时文件
    再让 pe.parse 读回来 ✅ → 每轮 48+ 次白写白读（A403 同类 ✅）。
    """
    deps: dict[str, list[str]] = {}
    for name, blob in entries.items():
        if not name.lower().endswith((".dll", ".pyd")):
            continue
        try:
            info = pe.parse_bytes(blob, Path(name))
            deps[name] = sorted(info.imports.keys(), key=str.lower)
        except Exception as exc:  # noqa: BLE001 — 解析失败只记录，不中断构建
            deps[name] = [f"<解析失败: {exc}>"]
    return deps


# 项目自有顶层包名 ✅（第三方收集时排除 ✅ —— 它们由 collect_project / collect_native 负责 ✅）
PROJECT_TOPS = defaults.PROJECT_TOPS  # 配置驱动（见 defaults.py）


# **编译管线版本** ✅：改写/编译选项变化但源码不变时 ✅
# 旧缓存不能复用 ✅（实测 clean vs 增量 typing_extensions.pyc 不一致 ✅）。
PYC_PIPELINE_VERSION = "cratix-1"


def _require_plain_dir(path: Path, label: str) -> None:
    """**构建阶段目录不能是链接/junction** ✅：否则写入/清理会越界 ✅。"""
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise SystemExit(f"[payload] 拒绝：{label} 是符号链接/目录连接：{path}")
    # **存在但不是目录** ✅：旧行为会在 copytree/mkdir 处抛堆栈 ✅。
    if path.exists() and not path.is_dir():
        raise SystemExit(f"[payload] 拒绝：{label} 存在但不是目录：{path}")


def clear_leaf(path: Path) -> None:
    """删除单个叶子路径（文件/目录/链接）。

    **junction/symlink 只删链接本身**：实测 rmtree(junction) 在 Python 3.14 是 no-op，
    旧写法「rmtree 或 unlink」遇到 junction 会留下目录 → 后续 replace/link 写入失败。
    """
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_atomic(path: Path, data: bytes) -> None:
    """**原子写缓存标记/ sidecar** ✅：直接 write_text 会跟随 symlink/hardlink ✅
    → 写到外部目标 ✅。先写 tmp 再 os.replace ✅（断开链接 ✅）。"""
    # **tmp 名字也不能被预先占用的 hardlink/symlink 引导** ✅
    # → 固定 .tmp 会被攻击者预先建成链接 ✅；用 pid+ns 唯一名 ✅。
    # **目录联接/目录符号链接只删链接本身** ✅：rmtree 对 junction 是 no-op ✅
    # （实测 rmtree(junction, ignore_errors=True) 后链接仍在 → os.replace WinError 5 ✅）。
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            pass
    elif path.is_dir():
        # **目标也可能是目录** ✅：os.replace 会失败 ✅（A222/A223/A245/A246/A247 同类 ✅）。
        shutil.rmtree(path, ignore_errors=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _pyc_valid(blob: bytes) -> bool:
    """**不只看 magic ✅**：16 字节截断的 .pyc 也带正确 magic ✅
    → zipimport 仍会拒载 ✅。用 marshal 解码 code object 确认完整 ✅。"""
    if len(blob) < 16:
        return False
    try:
        import importlib.util as _ilu2
        import marshal
        import types

        return blob[:4] == _ilu2.MAGIC_NUMBER and isinstance(marshal.loads(blob[16:]), types.CodeType)
    except ValueError, EOFError, TypeError:
        return False


def _pyc_entry(
    src: Path,
    rel: str,
    stage: Path,
    entries: dict[str, bytes],
    text: str | None = None,
    pre_digest: str | None = None,
    pre_dest: tuple[bytes | None, float | None, str | None] | None = None,
) -> None:
    """一个 .py → 未校验哈希 .pyc 条目（dfile 用 <cratix>/ 虚拟前缀 ✅ G5 ✅）。

    text ✅：改写后的源码（None = 原文件 ✅）。改写只发生在暂存副本 ✅ 行数不变 ✅。
    pre_digest ✅（A405）：调用方**并行预读**的源码 sha256 ✅ —— 命中路径免掉串行源码读
    （本机 open ~0.4ms ✅ 316 个文件 ≈ 0.3 s/轮 ✅）；改写路径（text 非 None ✅）
    摘要必须取暂存副本 ✅ → pre_digest 一律不用 ✅。
    pre_dest ✅（A407）：调用方**并行预读**的 (dest 字节, dest mtime, sidecar 文本) ✅
    —— dest+sidecar 两次串行读 ~0.3 s/轮 ✅；校验逻辑不变 ✅ 只是 I/O 并行 ✅；
    None = 旧行为（串行读 ✅ compile_pyc 等调用方不传 ✅）。

    磁盘文件名也要以 **.pyc** 收尾 ✅ —— py_compile 的 cfile 可以是任意名字 ✅
    但探针按「条目名」从磁盘摆树 ✅ → 名字对不上 ✅ → FileNotFoundError ✅（实测 ✅；
    载荷条目名本来就对 ✅ → 只有探针环境受影响 ✅）。

    **增量 = 源码内容 sha256** ✅（**不看 mtime** ✅ 实测教训 ✅）：
    uv 安装时保留 **wheel 内的时间戳** ✅（实测 loguru 源文件 mtime = 2026-04-16 = 发布日 ✅
    不是安装日 ✅）→ **升级第三方包**时新源文件带着**过去的 mtime** 落地 ✅
    → 「暂存 .pyc 比源新」恒成立 ✅ → 旧版本字节码永远进包 ✅（陈旧依赖 ✅ 必然发生 ✅
    不是低概率 ✅）。sidecar 哈希不匹配 = 源码变了 → 必重编 ✅；内容没变 → 命中 ✅
    （~340 个文件每轮读一遍哈希 ≈ 几 MB ✅ 秒级以内 ✅）。
    """
    dest = stage / "pyc3rd" / (rel[:-3] + ".pyc")
    sidecar = stage / "pyc3rd" / (rel[:-3] + ".pyc.entry")
    src_arg: Path
    if text is not None:
        tmp_src = stage / "pyc3rd_src" / rel
        tmp_src.parent.mkdir(parents=True, exist_ok=True)
        # **暂存改写源也用原子写** ✅：预置 hardlink/symlink 会让 write_text 写外部 ✅
        # （A240 只修了 cyc/project 的暂存 ✅ —— 这条漏了 ✅）。
        _write_atomic(tmp_src, text.encode("utf-8"))
        src_arg = tmp_src
    else:
        src_arg = src
    # 缓存键 = 源码 sha256 **+ 解释器缓存标签** ✅：只看源码内容 ✅ 会在**换解释器**
    # （3.14 → 3.15 / 自由线程构建 ✅）后继续复用旧 magic 的 .pyc ✅ → zipimport 拒载 ✅
    # （标准库那条缓存有解释器指纹 ✅，第三方这条原先没有 ✅）。
    # **optimize 也进 key** ✅：py_compile 默认用 sys.flags.optimize ✅ →
    # 普通构建与 python -O 构建产物不同 ✅；旧 key 只看源码+cache_tag ✅
    # → 两者互相复用旧字节码 ✅（-O 下的 pyc 丢 assert ✅）。
    if text is None and pre_digest is not None:
        _src_sha = pre_digest
    else:
        _src_sha = hashlib.sha256(src_arg.read_bytes()).hexdigest()
    digest = (
        _src_sha
        + "|"
        + (sys.implementation.cache_tag or "")
        + "|opt="
        + str(sys.flags.optimize)
        + "|v="
        + PYC_PIPELINE_VERSION
    )
    # **缓存 .pyc 也要校验** ✅：旧口径只看 sidecar ✅ →
    # .pyc 被截断/写坏后仍命中 ✅ → 空/坏字节码进包 ✅（zipimport 拒载 ✅）。
    # sidecar：digest + dest 内容 sha256。命中只做哈希，不 marshal 解码；
    # 任何 pyc 内容变化（含旧缓存格式/错误复用）都会重编。
    # **dest 只读一次** ✅：旧口径命中路径把 dest 读两遍（校验一遍 + 进包再读一遍 ✅）✓
    # 编译路径读三遍 ✅ —— 每次 open 在本机实测 ~0.4ms（FS 过滤器开销 ✅）
    # → 865 条目 × 1-2 次白读 ≈ 0.3 s/轮 ✅。
    # **pre_dest** ✅（A407）：dest+sidecar 的 I/O 由调用方共享读池并行完成 ✅（校验逻辑不变 ✅）。
    dest_bytes: bytes | None = None
    dest_mtime: float | None = None
    _sidecar_text: str | None = None
    if pre_dest is not None:
        dest_bytes, dest_mtime, _sidecar_text = pre_dest
    elif dest.is_file():
        try:
            dest_stat = dest.stat()
            if dest_stat.st_size >= 16:
                dest_bytes = dest.read_bytes()
                dest_mtime = dest_stat.st_mtime
        except OSError:
            dest_bytes = None
    _sidecar_ok = False
    if dest_bytes is not None:
        try:
            if _sidecar_text is None:
                _sidecar_text = sidecar.read_text(encoding="utf-8", errors="replace")
            _parts = _sidecar_text.split("\n", 1)
            if len(_parts) == 2 and _parts[0] == digest:
                _sidecar_ok = _parts[1] == hashlib.sha256(dest_bytes).hexdigest()
        except OSError:
            _sidecar_ok = False
    fresh = _sidecar_ok and dest_bytes is not None
    _blob = dest_bytes
    if fresh:
        # **命中刷新 mtime** ✅：_prune_old_files 看 mtime ✅ → 不刷新的话 ✅
        # 天天用的 .pyc 缓存也会在 30 天后被删 ✅（下次重编 ✅）。
        # **条件刷新** ✅：mtime 还在 7 天内就跳过 utime ✅（30 天清理怎么也删不到 ✅）——
        # 旧口径每轮命中 2 次 utime（实测 ~0.5ms/次 ✅ 865 条目 ×2 ≈ 0.5 s/轮 ✅）。
        if dest_mtime is None or dest_mtime < time.time() - 7 * 86400:
            try:
                os.utime(dest, None)
                os.utime(sidecar, None)
            except OSError:
                pass
    if not fresh:
        # **编译失败不能略旧产物** ✅：旧 pyc 在 dest 里 ✅ →
        # compile_pyc 失败后 dest 仍存在 ✅ → 旧代码进包 + sidecar 被写成新摘要 ✅
        # → 下次永远命中旧产物 ✅（实测：语法错误源码 + 旧 pyc ✅）。
        clear_leaf(dest)
        clear_leaf(sidecar)
        ok = compile_pyc(src_arg, dest, dfile=f"<cratix>/{rel}")
        # **刚编译出的 .pyc 也要验 magic** ✅：编译器/磁盘异常 ✅
        # → 坏字节码进包 ✅（旧口径只看 compile_pyc 返回值 ✅）。
        if ok and dest.exists():
            try:
                import importlib.util as _ilu2

                _blob = dest.read_bytes()
                if not _pyc_valid(_blob):
                    ok = False
            except OSError:
                ok = False
        if ok and dest.exists():
            _dest_sha = hashlib.sha256(_blob).hexdigest()
            _write_atomic(sidecar, (digest + "\n" + _dest_sha).encode("utf-8"))
        else:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            # **第三方 .py 编译失败也要构建失败** ✅：旧行为静默跳过 ✅
            # → 产物缺模块 ✅ → 运行期 ModuleNotFoundError ✅。
            raise SystemExit(f"[payload] 第三方 .py 编译失败（构建失败）：{rel}")
    if _blob is not None:
        entries[rel[:-3] + ".pyc"] = _blob


def _require_pe(path: Path, label: str) -> bytes:
    """读 PE 并校验 MZ ✅；非 PE → 构建失败 ✅。"""
    blob = path.read_bytes()
    if len(blob) < 2 or not blob.startswith(b"MZ"):
        raise SystemExit(f"[payload] {label} 非 PE（构建失败）：{path}")
    return blob


def _read_source_text(path: Path) -> str:
    """按 PEP 263 编码声明读第三方源码 ✅（旧口径 utf-8+replace 会把 GBK 字面量变 U+FFFD ✅）。"""
    import tokenize

    try:
        with tokenize.open(path) as handle:
            return handle.read()
    except OSError, SyntaxError, UnicodeDecodeError:
        # **读失败必须抛出**（旧写法返回 "" → 空模块静默进包）。
        return path.read_text(encoding="utf-8", errors="replace")


def _collect_top_level_pyd(top: str, site_packages: Path, entries: dict[str, bytes], extensions: list[dict]) -> bool:
    """收集**顶层**的 .pyd 模块 ✅（_cffi_backend.cp314-win_amd64.pyd ✅ 这类 ✅）。

    顶层 .pyd 不在包目录里 ✅ → 按包收集时看不见 ✅ → import 时 ModuleNotFoundError ✅（实测 ✅）。
    """
    cands = sorted(site_packages.glob(f"{top}.*.pyd")) + sorted(site_packages.glob(f"{top}.pyd"))
    _bad: list[str] = []
    for pyd in cands:
        # **模块名 / init 必须用「导入名」top** ✅：文件名大小写可能与导入名不同 ✅
        # （Windows 允许 Foo.cp314-win_amd64.pyd 被 import foo ✅）——
        # 旧口径用文件名 stem ✅ → 模块登记成 Foo ✅ → finder 查 foo 查不到 ✅、
        # init 也会找 PyInit_Foo ✅（真实导出是 PyInit_foo ✅）。
        entry = f"ext3rd/{top}.pyd"
        _blob = pyd.read_bytes()
        if len(_blob) < 2 or not _blob.startswith(b"MZ"):
            # **多候选时继续找有效的** ✅：旧行为第一个坏就失败 ✅
            # → 后面的好候选（foo.pyd ✅）被忽略 ✅。
            _bad.append(pyd.name)
            continue
        entries[entry] = _blob
        extensions.append(
            {
                "module": top,
                "entry": entry,
                "init": f"PyInit_{top}",
                "kind": "module",
                "virtual": f"<exe>/{top}.pyd",
            }
        )
        return True
    if _bad:
        raise SystemExit(f"[payload] 顶层 .pyd 非 PE（构建失败）：{', '.join(_bad)}")
    return False


def _collect_package_dir(
    top: str,
    pkg_dir: Path,
    site_packages: Path,
    stage: Path,
    entries: dict[str, bytes],
    extensions: list[dict],
    only_py: bool = False,
) -> None:
    """递归收集一个第三方包目录 ✅：.py → .pyc、.pyd → ext3rd/ + 扩展记录、其余 → 数据条目。

    条目路径与 site-packages 同构 ✅（loguru/__init__.pyc 在 zip 根 ✅）——
    运行时包的 __path__ = [exe] ✅ → 子模块与包数据都能被 zipimport / VFS 解析 ✅。
    only_py=True ✅：只收纯 Python 部分 ✅（PySide6 整包 657 MB ✅ —— 原生部分由
    collect_native 按最小集收集 ✅ 剩下的 DLL/插件不该进包 ✅）。

    **os.walk 取代 rglob** ✅（A404）：rglob 的 glob 机制 + 逐条 relative_to/Path 构造
    实测 ~1.5 s（16 包 ~4.6k 文件 ✅）——walk 用字符串过滤 ✅ 只为真正消费的文件建 Path ✅。
    **顺序必须与旧口径逐字节一致** ✅（条目插入序 = zip 条目序 ✅ A9 ✅）：
    旧口径 = `sorted(rglob)` = Path 的 **normcase 分部元组**比较 ✅ → 排序键用
    `tuple(part.lower() ...)` 复刻 ✅（16 包过滤后清单逐项比对 0 差异 ✅ 实测 ✅）。
    剪掉的 `__pycache__`/`*.dist-info` 目录 ✅：其成员本就被下面的过滤跳过 ✅ → 等价 ✅。
    """
    sp_prefix = str(site_packages) + os.sep
    walked: list[str] = []
    for dirpath, dirnames, filenames in os.walk(pkg_dir, topdown=True, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.endswith(".dist-info"))
        for fn in filenames:
            walked.append(dirpath + os.sep + fn)
    walked.sort(key=lambda s: tuple(part.lower() for part in s.split(os.sep)))
    # **源码摘要并行预读** ✅（A405）：.py 的 sha256 是缓存键 ✅ 内容口径不变（同一份原始字节 ✅）
    # —— 串行读+哈希 ~0.3 s/轮（open ~0.4ms ✅）→ 线程池 ✅；
    # 改写文件（PySide6 __init__.py ✅）的摘要必须取**暂存副本** ✅ → 不预读 ✅（_pyc_entry 串行算 ✅）。
    _pre_digests: dict[str, str] = {}
    _py_targets = [
        full
        for full in walked
        if full.rsplit(os.sep, 1)[-1].lower().endswith(".py")
        and not (top == "PySide6" and full.rsplit(os.sep, 1)[-1].lower() == "__init__.py")
    ]
    _py_targets_rel = [
        full[len(sp_prefix) :].replace(os.sep, "/") if full.startswith(sp_prefix) else None for full in _py_targets
    ]
    if len(_py_targets) > 4:
        # **共享读池** ✅（A405）：每个包目录自建池的开销实测 > 收益 ✅。
        def _sha_of(full: str):
            try:
                return hashlib.sha256(Path(full).read_bytes()).hexdigest()
            except OSError:
                return None

        for full, d in zip(_py_targets, _io_pool().map(_sha_of, _py_targets)):
            if d is not None:
                _pre_digests[full] = d
    # **dest+sidecar 并行预读** ✅（A407）：_pyc_entry 的命中校验需要 dest 字节 + sidecar ✅
    # —— 串行 2 次 open/条目 ~0.3 s/轮 ✅ → 共享读池并行 ✅；校验逻辑在 _pyc_entry 内不变 ✅；
    # 预读的是「当前磁盘状态」✅ 与串行同源 ✅（单线程处理无并发写者 ✅）。
    _pre_dests: dict[str, tuple[bytes | None, float | None, str | None]] = {}

    def _read_dest(rel_key: str):
        _d = stage / "pyc3rd" / (rel_key[:-3] + ".pyc")
        _sc = stage / "pyc3rd" / (rel_key[:-3] + ".pyc.entry")
        try:
            if not _d.is_file():
                return None, None, None
            _st = _d.stat()
            if _st.st_size < 16:
                return None, _st.st_mtime, None
            _b = _d.read_bytes()
            try:
                _s = _sc.read_text(encoding="utf-8", errors="replace")
            except OSError:
                _s = None
            return _b, _st.st_mtime, _s
        except OSError:
            return None, None, None

    if len(_py_targets) > 4:
        _dest_rel_keys = [r for r in _py_targets_rel if r is not None]
        for rel_key, _pd in zip(_dest_rel_keys, _io_pool().map(_read_dest, _dest_rel_keys)):
            _pre_dests[rel_key] = _pd
    for full in walked:
        if not full.startswith(sp_prefix):
            # **防御** ✅：前缀不合（符号链接逃逸 site-packages 等 ✅）→ 旧口径同语义 ✅。
            path = Path(full)
            rel = path.relative_to(site_packages).as_posix()
        else:
            path = None
            rel = full[len(sp_prefix) :].replace(os.sep, "/")
        parts = rel.split("/")
        if "__pycache__" in parts or any(p.endswith(".dist-info") for p in parts):
            continue
        _lower = rel.rsplit("/", 1)[-1].lower()
        _suffix = _lower[_lower.rfind(".") + 1 :] if "." in _lower else ""
        _suffix = f".{_suffix}" if _suffix else ""
        if _suffix == ".py":
            src = path or Path(full)
            text: str | None = None
            if top == "PySide6" and rel.rsplit("/", 1)[-1] == "__init__.py":
                # cratix 改写 ✅（M4 实测 ✅）：_additional_dll_directories 的目录探测
                # （Path(__file__) + is_dir）在内存加载下不成立 ✅ —— 缺 libshiboken 目录
                # 就 raise ✅ 实测 ImportError ✅。shiboken6 的加载由加载桥接管 ✅
                # 插件路径由 QT_PLUGIN_PATH + VFS 接管 ✅ → raise 行改写为 return [] ✅
                # （暂存副本改写 ✅ 行数不变 ✅ 仓库源码不动 ✅；_setupQtDirectories 里
                #   的 Shiboken 导入必须保留 ✅ —— 模块体后面要用 Shiboken.replaceModuleDict ✅）。
                _pyside_src = _read_source_text(src)
                _old_line = "        raise ImportError(str(shiboken6) + ' does not exist')"
                text = _pyside_src.replace(
                    _old_line,
                    "        return []  # cratix: 内存加载下 DLL 目录探测不适用 ✅",
                    1,
                )
                # **改写没命中必须构建失败** ✅：行还在（缩进/上下文变了）但没替换 ✅
                # → 内存加载下 PySide6 初始化必 ImportError ✅，而旧行为静默出包 ✅
                # （只有 verify_no_console 才能发现 ✅）。
                # **标记放宽到前缀** ✅：版本变化可能只改引号 ✅ ——
                # 只匹配单引号整行会让「双引号变体」绕过检查 ✅ → 静默出包 ✅。
                if text == _pyside_src and "raise ImportError(str(shiboken6)" in _pyside_src:
                    raise SystemExit("[payload] PySide6/__init__.py 的 cratix 改写未命中（PySide6 版本变化？）")
            _pyc_entry(
                src, rel, stage, entries, text=text, pre_digest=_pre_digests.get(full), pre_dest=_pre_dests.get(rel)
            )
        elif _suffix == ".pyd" and not only_py:
            src = path or Path(full)
            entry = f"ext3rd/{rel}"
            _blob = src.read_bytes()
            # **第三方 .pyd 也必须是 PE（MZ）** ✅：截断/占位文件 ✅
            # 进包后加载不了 ✅ → 构建失败 ✅。
            if len(_blob) < 2 or not _blob.startswith(b"MZ"):
                raise SystemExit(f"[payload] 第三方 .pyd 非 PE（构建失败）：{rel}")
            entries[entry] = _blob
            stem = rel.rsplit("/", 1)[-1].split(".")[0]
            # 模块名要把 **版本标签一起剥掉** ✅：_core.cp314-win_amd64.pyd 的模块是
            # msgspec._core ✅ —— rel[:-4] 只剥 .pyd ✅ → 名字里带着标签 ✅
            # → finder 查不到 ✅ → ModuleNotFoundError ✅（实测 ✅）
            parent = rel[: rel.rfind("/") + 1].rstrip("/") if "/" in rel else ""
            # 模块名用**点号** ✅：parent 还是路径分隔的斜杠 ✅ → 直接拼会得到
            # "cryptography/hazmat/bindings._rust" ❗（finder 查不到 ✅ → ModuleNotFoundError ✅）
            parent_mod = parent.replace("/", ".")
            module = f"{parent_mod}.{stem}" if parent_mod else stem
            extensions.append(
                {
                    "module": module,
                    "entry": entry,
                    "init": f"PyInit_{stem}",
                    "kind": "module",
                    "virtual": f"<exe>/{rel}",
                }
            )
        elif _suffix in {".pyi", ".pyc"}:
            continue  # 存根 / 中间产物不进包 ✅
        elif not only_py:
            src = path or Path(full)
            # **包数据里的 DLL 也要校验 PE** ✅：截断占位 DLL ✅
            # 进包后加载不了 ✅ → 构建失败 ✅。
            if _suffix in {".dll", ".pyd"}:
                entries[rel] = _require_pe(src, "包数据 DLL")
            else:
                entries[rel] = src.read_bytes()  # 包数据 ✅（pypinyin 词表 / certifi 证书等 ✅）


def _collect_single_module(
    top: str, src: Path, site_packages: Path, stage: Path, entries: dict[str, bytes], extensions: list[dict]
) -> None:
    """单文件第三方模块（typing_extensions.py ✅ / wreq.pyd 之类 ✅）。"""
    _src_suffix = src.suffix.lower()
    if _src_suffix == ".py":
        _pyc_entry(src, src.name, stage, entries)
    elif _src_suffix == ".pyd":
        entry = f"ext3rd/{src.name}"
        _blob = src.read_bytes()
        if len(_blob) < 2 or not _blob.startswith(b"MZ"):
            raise SystemExit(f"[payload] 第三方 .pyd 非 PE（构建失败）：{src.name}")
        entries[entry] = _blob
        stem = src.name.split(".")[0]
        extensions.append(
            {
                "module": stem,
                "entry": entry,
                "init": f"PyInit_{stem}",
                "kind": "module",
                "virtual": f"<exe>/{src.name}",
            }
        )


def _collect_pyside6(site_packages: Path, stage: Path, entries: dict[str, bytes], extensions: list[dict]) -> None:
    """PySide6：只收纯 Python 部分 ✅（整包 657 MB ✅ —— 原生按最小集由 collect_native 收 ✅）。"""
    pkg_dir = site_packages / "PySide6"
    _collect_package_dir("PySide6", pkg_dir, site_packages, stage, entries, extensions, only_py=True)
    for pyd in ("QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd"):
        extensions.append(
            {
                "module": f"PySide6.{pyd[:-4]}",
                "entry": f"native/PySide6/{pyd}",
                "init": f"PyInit_{pyd[:-4]}",
                "kind": "module",
                "virtual": f"<exe>/PySide6/{pyd}",
            }
        )


def _collect_shiboken6(site_packages: Path, stage: Path, entries: dict[str, bytes], extensions: list[dict]) -> None:
    """shiboken6：只收 .py 与 Shiboken.pyd ✅。

    include/ ✅ lib/ ✅ VC 运行库 DLL ✅ 都是**构建期**文件 ✅ —— 运行时零引用 ✅。
    ⚠ 这个特例必须在**两条发现路径**上都生效 ✅：项目直接 import 的 shiboken6 ✅ 与
    从 PySide6 的 import **递归发现**的 shiboken6 ✅ —— 后者原先落到通用 _collect_package_dir ✅
    → 整个包（2.9 MB ✅ 含 .h/.lib/.dll/.cmake ✅）进载荷 ✅（实测 payload 里 66 个条目 ✅）。
    """
    pkg_dir = site_packages / "shiboken6"
    for py in sorted(pkg_dir.glob("*.py")):
        _pyc_entry(py, f"shiboken6/{py.name}", stage, entries)
    shib = pkg_dir / "Shiboken.pyd"
    if shib.is_file():
        # **Shiboken.pyd 也要校验 PE** ✅：截断/占位文件进包后 LoadLibrary 失败 ✅
        # （A143-A151 的 PE 校验漏了这一条 ✅）。
        entries["ext3rd/shiboken6/Shiboken.pyd"] = _require_pe(shib, "shiboken6.Shiboken.pyd")
        extensions.append(
            {
                "module": "shiboken6.Shiboken",
                "entry": "ext3rd/shiboken6/Shiboken.pyd",
                "init": "PyInit_Shiboken",
                "kind": "module",
                "virtual": "<exe>/shiboken6/Shiboken.pyd",
            }
        )


def _io_pool():
    """**共享读池** ✅（A405）：每次调用各建 ThreadPoolExecutor 实测反而变慢 ✅
    （29 次 _third_party_imports + 13 个包目录 = 42 池 × 8 线程的创建开销 ✅
    Windows 线程栈分配 ~5-20ms/池 ✅ > 小目录的并行收益 ✅）→ 进程级单池 ✅。"""
    global _IO_POOL
    if _IO_POOL is None:
        import concurrent.futures as futures

        _IO_POOL = futures.ThreadPoolExecutor(min(8, (os.cpu_count() or 4) // 2))
    return _IO_POOL


_IO_POOL = None


def _third_party_imports(pkg_dir: Path, cache: dict | None = None) -> set[str]:
    """扫第三方包的顶层 import ✅。

    依赖闭包只扫**项目代码** ✅ → 第三方包**自己**的依赖看不见 ✅
    （loguru 在 Windows 上无条件 `import win32_setctime` ✅ → 不收 ✅ → 真实入口当场崩 ✅）。
    cache 可选：path -> {"sha1": 源码摘要, "imports": [...]}，跨构建复用 AST 结果。
    """
    import ast
    import hashlib

    tops: set[str] = set()
    # pkg_dir 也可以是**单文件模块**（foo.py）—— 旧口径只收包目录
    # → 「单文件第三方模块自己的依赖」根本没进闭包（实测：foo.py import bar → 只收 foo）。
    if pkg_dir.is_file():
        paths: list[Path] = [pkg_dir]
    else:
        # **os.walk 取代 rglob("*.py")** ✅（A405）：glob 机制 ~0.4s ✅；
        # 结果是集合并集 ✅ 顺序无关 ✅（调用方 sorted(queue) ✅）。
        paths = []
        for dirpath, dirnames, filenames in os.walk(pkg_dir, topdown=True, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            base = Path(dirpath)
            for fn in filenames:
                if fn.endswith(".py"):
                    paths.append(base / fn)

    def _load(py: Path):
        """读 + 哈希 + 缓存命中提取 ✅：worker 内对共享 cache **只读** ✅（GIL 下安全 ✅）；
        返回 (text, digest, hit_imports) —— hit_imports 非空 = 命中 ✅。"""
        try:
            text = _read_source_text(py)
        except OSError:
            return None, None, None
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if cache is not None:
            hit = cache.get(str(py))
            if isinstance(hit, dict) and hit.get("sha1") == digest and isinstance(hit.get("imports"), list):
                return None, None, tuple(str(name) for name in hit["imports"])
        return text, digest, None

    misses: list[tuple[Path, str, str]] = []
    if len(paths) > 4:
        # **并行读+哈希** ✅（A405）：793 个文件的本机 open ~0.4ms ✅ 串行 ~0.6s ✅
        # → 读与 sha1 释放 GIL ✅；**共享池** ✅（各调用自建池的创建开销实测 > 收益 ✅）；
        # AST 解析/缓存**写**留在主线程 ✅。
        for py, text, digest, hit_imports in _io_pool().map(lambda p: (p, *_load(p)), paths):
            if hit_imports is not None:
                tops.update(hit_imports)
            elif text is not None:
                misses.append((py, text, digest))
    else:
        for py in paths:
            text, digest, hit_imports = _load(py)
            if hit_imports is not None:
                tops.update(hit_imports)
            elif text is not None:
                misses.append((py, text, digest))
    for py, text, digest in misses:
        try:
            tree = ast.parse(text)
        except SyntaxError, ValueError:
            continue
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                found.add(node.module.split(".")[0])
        tops.update(found)
        if cache is not None:
            cache[str(py)] = {"sha1": digest, "imports": sorted(found)}
    return tops


def _collect_companion_libs(top: str, site_packages: Path, entries: dict[str, bytes]) -> None:
    """
    **递归发现的包也必须收** ✅：旧口径只在第一层循环里收 ✅ →
    foo import bar、bar 带 bar.libs 时，bar 的原生依赖不进包 ✅ → 运行期加载失败 ✅。
    """
    libs_dir = site_packages / f"{top}.libs"
    if not libs_dir.is_dir():
        return
    for path in sorted(libs_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(site_packages).as_posix()
            # **.libs 里的 DLL/pyd 也要校验 PE** ✅：截断占位文件 ✅
            # 进包后加载不了 ✅。
            if path.suffix.lower() in {".dll", ".pyd"}:
                entries[rel] = _require_pe(path, ".libs 原生文件")
            else:
                entries[rel] = path.read_bytes()


def collect_third_party(closure, site_packages: Path, stage: Path, log=print) -> dict:
    """第三方依赖进包（§2.2「纯 Python 包 zipimport 直读 ✅」/ §5.2 ext3rd ✅）。

    范围 = 闭包里 import 到的第三方顶层包 ✅（closure.top_level ✅），
    减去标准库 / 项目自有 / 裁剪表 ✅。
    特例 ✅：
      - PySide6：只收纯 Python 部分 ✅（__init__ ✅）—— 原生 pyd/DLL 由 collect_native
        按「Qt 最小集」收集 ✅（整包 657 MB ✅ 只需要 Core/Gui/Widgets ✅），
        三个绑定 pyd 的扩展记录指向 native/ 条目 ✅；
      - shiboken6：只收 .py 与 Shiboken.pyd ✅（include/lib/VC 运行库 DLL 不进包 ✅）。
    伴生目录 ✅：<top>.libs（rapidfuzz 的运行期 DLL ✅ 原生扩展按相对路径加载 ✅）按数据收 ✅。
    """
    _require_plain_dir(stage / "pyc3rd", "stage/pyc3rd")
    _require_plain_dir(stage / "pyc3rd_src", "stage/pyc3rd_src")
    # **编译管线/优化级变化就清空 pyc3rd** ✅：
    # 旧 sidecar 与字节码可能在 -O 与普通构建之间交叉污染 ✅
    # （实测应用层字节码不一致 ✅）。
    _want_cache_ver = f"{sys.version}|{sys.flags.optimize}|{PYC_PIPELINE_VERSION}"
    _cache_ver = stage / "pyc3rd.version"
    try:
        _old_ver = _cache_ver.read_text(encoding="utf-8") if _cache_ver.is_file() else ""
    except OSError:
        _old_ver = ""
    if _old_ver != _want_cache_ver:
        shutil.rmtree(stage / "pyc3rd", ignore_errors=True)
        shutil.rmtree(stage / "pyc3rd_src", ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        if _cache_ver.is_dir() and not _cache_ver.is_symlink():
            shutil.rmtree(_cache_ver, ignore_errors=True)
        _write_atomic(_cache_ver, _want_cache_ver.encode("utf-8"))
    else:
        # **sidecar 异常（目录/链接）就清全缓存** ✅：
        # 旧行为只处理当前条目 ✅ → 可能交叉污染 ✅（A222 实测 ✅）。
        _cache_dir = stage / "pyc3rd"
        _bad_side = False
        if _cache_dir.is_dir():
            for _side in _cache_dir.rglob("*.sha"):
                if _side.is_symlink() or (hasattr(_side, "is_junction") and _side.is_junction()) or not _side.is_file():
                    _bad_side = True
                    break
        if _bad_side:
            shutil.rmtree(stage / "pyc3rd", ignore_errors=True)
            shutil.rmtree(stage / "pyc3rd_src", ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=True)
            if _cache_ver.is_dir() and not _cache_ver.is_symlink():
                shutil.rmtree(_cache_ver, ignore_errors=True)
            _write_atomic(_cache_ver, _want_cache_ver.encode("utf-8"))
    import sys as _sys

    entries: dict[str, bytes] = {}
    extensions: list[dict] = []
    tops: list[str] = []
    imported_tops: set[str] = set()
    queue: list[str] = []
    # **同一包的 AST 扫描只做一次**：下面初始/递归路径会对同一个 pkg_dir 重复调用
    # _third_party_imports；实测 29 次调用 → 6.5s，其中一半是重复扫描。
    _imports_cache: dict[Path, set[str]] = {}
    import hashlib as _hashlib
    import json as _json

    _code_hash = _hashlib.sha1(Path(__file__).read_bytes()).hexdigest()
    _ast_cache_path = stage.parent.parent / "third_imports.json"
    _ast_cache: dict = {}
    try:
        _ast_cache = _json.loads(_ast_cache_path.read_text(encoding="utf-8"))
        if (
            not isinstance(_ast_cache, dict)
            or _ast_cache.get("__version__") != 1
            or _ast_cache.get("__code__") != _code_hash
        ):
            _ast_cache = {}
    except OSError, ValueError:
        _ast_cache = {}
    _ast_cache["__version__"] = 1
    _ast_cache["__code__"] = _code_hash

    def _imports(path: Path) -> set[str]:
        hit = _imports_cache.get(path)
        if hit is None:
            hit = _third_party_imports(path, _ast_cache)
            _imports_cache[path] = hit
        return hit

    for top in sorted(closure.top_level):
        if top in _sys.stdlib_module_names:
            continue
        if top in PROJECT_TOPS or defaults.is_excluded_package(top) or defaults.is_excluded_module(top):
            continue
        pkg_dir = site_packages / top
        if pkg_dir.is_dir():
            if top == "PySide6":
                _collect_pyside6(site_packages, stage, entries, extensions)
            elif top == "shiboken6":
                _collect_shiboken6(site_packages, stage, entries, extensions)
            else:
                _collect_package_dir(top, pkg_dir, site_packages, stage, entries, extensions)
        elif (site_packages / f"{top}.py").is_file():
            _collect_single_module(top, site_packages / f"{top}.py", site_packages, stage, entries, extensions)
            # **单文件模块自己的依赖也要进闭包**（旧口径只扫包目录 —— 实测 foo.py import bar 时只收 foo）。
            _imps = _imports(site_packages / f"{top}.py")
            imported_tops |= _imps
            queue.extend(sorted(_imps))
        else:
            # **带版本标签的顶层 pyd 也要认**：旧口径只查精确 foo.pyd →
            # foo.cp314-win_amd64.pyd（二进制 wheel 的默认形态）直接「找不到」。
            if not _collect_top_level_pyd(top, site_packages, entries, extensions):
                log(f"[payload] 第三方包在 site-packages 里找不到：{top}")
                continue
        # 伴生目录：<top>.libs（原生扩展按相对路径加载的 DLL ✅）
        _collect_companion_libs(top, site_packages, entries)
        if pkg_dir.is_dir():
            imported_tops |= _imports(pkg_dir)
        tops.append(top)
    # 第三方包**自己**的依赖 ✅（闭包只扫项目代码 ✅ → loguru 的 win32_setctime ✅ 看不见 ✅
    # → 不收 ✅ → 真实入口一启动就 ModuleNotFoundError ✅（实测 ✅））
    # **BFS 顺序必须确定** ✅：`_third_party_imports` 返回 **set** ✅
    # → 字符串集合的迭代顺序受 PYTHONHASHSEED 随机化 ✅ → 递归发现的包
    # （win32_setctime/colorama/typing_extensions/shiboken6 ✅）每轮进程顺序漂移 ✅
    # → `tops` 顺序漂移 ✅ → meta.json/third_party_tops/qt_native 漂移 ✅
    # → **载荷字节不可复现** ✅（实测两次构建 sha256 不同 ✅ 唯一根因 ✅）。
    for top in sorted(tops):
        pkg_dir = site_packages / top
        if pkg_dir.is_dir():
            queue.extend(sorted(_imports(pkg_dir)))
    while queue:
        top = queue.pop(0)
        if top in tops or top in _sys.stdlib_module_names or top in PROJECT_TOPS:
            continue
        if defaults.is_excluded_package(top) or defaults.is_excluded_module(top):
            continue
        pkg_dir = site_packages / top
        found = False
        if pkg_dir.is_dir():
            # **特例在递归发现路径上同样生效** ✅（PySide6 会 import shiboken6 ✅ → 走这里 ✅）
            if top == "PySide6":
                _collect_pyside6(site_packages, stage, entries, extensions)
            elif top == "shiboken6":
                _collect_shiboken6(site_packages, stage, entries, extensions)
            else:
                _collect_package_dir(top, pkg_dir, site_packages, stage, entries, extensions)
            imported_tops |= _imports(pkg_dir)
            queue.extend(sorted(_imports(pkg_dir)))
            found = True
        elif (site_packages / f"{top}.py").is_file():
            _collect_single_module(top, site_packages / f"{top}.py", site_packages, stage, entries, extensions)
            _imps = _imports(site_packages / f"{top}.py")
            imported_tops |= _imps
            queue.extend(sorted(_imps))
            found = True
        elif _collect_top_level_pyd(top, site_packages, entries, extensions):
            found = True
        if found:
            # **递归发现路径也要收伴生 .libs** ✅（旧口径只在第一层收 ✅）。
            _collect_companion_libs(top, site_packages, entries)
            tops.append(top)

    # **顶层 .pyd 只收「被用到的」** ✅ —— 原口径是**全收** ✅：
    # Rust/C 扩展之间的依赖是**链接期**的 ✅（cryptography 的 _rust ✅ → _cffi_backend ✅）
    # —— AST 扫不到 ✅（不是 Python import ✅）→ 不收 ✅ → 运行期 ModuleNotFoundError ✅（实测 ✅）。
    # 但「全收」会把**构建期工具**的 mypyc 产物一起收进来 ✅ —— 实测 site-packages 根目录
    # 有一个 19.8 MB 的 mypyc 共享库 ✅（构建期工具 ✅ 运行期零引用 ✅）→ 白收 5.5 MiB ✅。
    # 口径 ✅：被已收集的包 import 到 ✅（AST ✅）+ 链接期依赖白名单 ✅。
    link_time_deps = {"_cffi_backend"}
    _bad_top: list[tuple[str, str]] = []
    for pyd in sorted(site_packages.glob("*.pyd")):
        stem = pyd.name.split(".")[0]
        # **大小写不敏感匹配导入名** ✅：Windows 允许 Foo.pyd 被 import foo ✅ ——
        # 旧口径字符串精确比较 ✅ → 整个扩展被当成「不被引用」跳过 ✅。
        mod = next((t for t in imported_tops if t.lower() == stem.lower()), None)
        if mod is None and stem not in link_time_deps:
            log(f"[payload] 顶层 .pyd 不被引用 ✅ 不进包：{pyd.name}（{pyd.stat().st_size / 1048576:.1f} MB ✅）")
            continue
        mod = mod or stem
        entry = f"ext3rd/{mod}.pyd"
        if entry in entries:
            continue
        _blob = pyd.read_bytes()
        if len(_blob) < 2 or not _blob.startswith(b"MZ"):
            # **多候选时先记下坏候选** ✅：后面可能有同 stem 的好 pyd ✅
            # → 旧行为第一个坏就失败 ✅。
            _bad_top.append((mod, pyd.name))
            continue
        entries[entry] = _blob
        extensions.append(
            {
                "module": mod,
                "entry": entry,
                "init": f"PyInit_{mod}",
                "kind": "module",
                "virtual": f"<exe>/{mod}.pyd",
            }
        )
        tops.append(mod)
        # **顶层 pyd 的伴生 .libs 也收** ✅（与其它发现路径同口径）。
        _collect_companion_libs(mod, site_packages, entries)
    _really_bad = [n for mod, n in _bad_top if f"ext3rd/{mod}.pyd" not in entries]
    if _really_bad:
        raise SystemExit(f"[payload] 顶层 .pyd 非 PE（构建失败）：{', '.join(_really_bad)}")

    # shiboken6 是 PySide6 硬依赖 ✅（PySide6/__init__.py: from shiboken6 import Shiboken ✅）
    # 但项目代码不直接 import 它 ✅ → 闭包顶层看不到 ✅ → 跟着 PySide6 一起收 ✅
    if "PySide6" in tops and "shiboken6" not in tops:
        shib_dir = site_packages / "shiboken6"
        if shib_dir.is_dir():
            _collect_shiboken6(site_packages, stage, entries, extensions)
            tops.append("shiboken6")
    log(f"[payload] 第三方包 {len(tops)} 个 / 条目 {len(entries)}")
    # **陈旧 .pyc 缓存清理** ✅：当前条目已读入内存 ✅ → 删掉 30 天未用的旧文件不影响本轮 ✅。
    _prune_old_files(stage / "pyc3rd", 30)
    # **改写临时源也要清** ✅：PySide6 等改写的暂存 .py ✅
    # 模块移除后一直堆着 ✅（虽小 ✅）。
    _prune_old_files(stage / "pyc3rd_src", 30)
    _prune_old_files(stage / "pyc", 30)
    try:
        _write_atomic(
            _ast_cache_path, _json.dumps(_ast_cache, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except OSError:
        pass
    return {"entries": entries, "extensions": extensions, "tops": tops}


# 压缩策略 ✅：
#   - **zlib1.dll 必须 STORED** ✅ —— 它就是解压器本身 ✅（鸡生蛋 ✅）；
#   - **entry.txt / meta.json 保持 STORED** ✅ —— 极小 ✅（stub 早期就要读 ✅）；
#   - **.pyc 保持 DEFLATE** ✅ —— zipimport 只认 STORED/DEFLATE ✅（标准库/回退集/第三方
#     纯 Python 都经 zipimport 读 ✅）；
#   - **其余二进制（.pyd/.dll/包数据 ✅）走 ZIP_ZSTANDARD（level 19）** ✅（M7 压缩换代 ✅）：
#     同头部条目实测 23.4 MB（DEFLATE）→ 19.2 MB（-18% ✅ exe 62.3 → ~58 MB），
#     解压快 ~70 倍于 LZMA（0.02s vs 1.43s ✅ §14）→ 启动期几乎零惩罚 ✅；
#     备选 ZIP_LZMA（17.6 MB / -25% 但启动 +1.7s ✅ 实测 3.43 → 5.11 s ✅）——
#     两者的解码器都已静态进 stub ✅（zipread.c ✅ 切换只改这里的返回值 ✅）。
#     读侧全覆盖 ✅：stub 用 libzstd/LzmaDec ✅ / Python 用 zipfile 原生支持 ✅
#     （B 形态提取 + bootstrap 零改动 ✅；compression.zstd 与 _lzma.pyd 都在载荷里 ✅）。
DEFLATE_LEVEL = 9
ZSTD_LEVEL = 19


def entry_compress_type(name: str) -> int:
    """按条目名决定压缩方式 ✅（见上面的策略注释 ✅）。"""
    # **扩展名大小写不敏感** ✅：大写 .PYC/.PYD 也要走对应策略 ✅。
    lname = name.lower()
    if lname in ("native/zlib1.dll", "entry.txt", "meta.json"):
        return zipfile.ZIP_STORED
    if (
        lname.startswith("extstd/")
        and lname.endswith(".pyd")
        and lname.split("/")[-1].rsplit(".", 1)[0]
        in (
            "_zstd",
            "_lzma",
        )
    ):
        # 只有解压器自身 STORED ✅（M7 ✅）：B 形态提取 zstd 载荷用的 zipfile 恰恰
        # 需要 _zstd ✅（鸡生蛋 ✅）——只豁免 _zstd/_lzma ✅ 其余 extstd 照常 zstd ✅
        return zipfile.ZIP_STORED
    if lname.endswith(".pyc"):
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_ZSTANDARD


def entry_compress_level(ctype: int) -> int:
    """按压缩方式给 level ✅（zipfile 的 ZstdCompressor 直通 level ✅）。"""
    if ctype == zipfile.ZIP_ZSTANDARD:
        return ZSTD_LEVEL
    return DEFLATE_LEVEL


def apply_solid_groups(entries: dict[str, bytes], manifest: dict, groups: dict[str, list[str]]) -> None:
    """把若干条目合成**一个**压缩流条目 ✅（solid group ✅ —— 原地改 entries 与 manifest ✅）。

    为什么 ✅：逐条压缩丢掉**跨条目冗余** ✅ —— Cython 产物每份都带 ~148 KiB 的
    样板代码 ✅（162 份 ✅ ≈ 23 MiB ✅）→ 逐条压 18.1 MiB ✅ / 整块压 6.0 MiB ✅（实测 ✅）。
    条目名改写成「组名#offset:size:count」✅ —— stub 的加载桥认这个形态 ✅
    （count ✅ 用于「全部条目服务完 ✅ → 释放整块缓冲 ✅」✅）。
    """
    for group_name, members in groups.items():
        members = [name for name in members if name in entries]
        if not members:
            continue
        blob = bytearray()
        spans: dict[str, tuple[int, int]] = {}
        for name in members:
            data = entries[name]
            spans[name] = (len(blob), len(data))
            blob += data
        entries[group_name] = bytes(blob)
        for name in members:
            del entries[name]
        # **切片数** ✅ = 组内**不同 pyd** 数 ✅（不是记录数 ✅）：stub 现在对已映射镜像做缓存 ✅
        # → 同一个 pyd 服务多个模块只 map 一次 ✅ → served 也只在「首次 map」时自增 ✅。
        # 若仍按记录数 ✅ → total 永远达不到 ✅ → 18 MB 整块缓冲永不释放 ✅。
        count = len(spans)
        for rec in manifest.get("extensions", []):
            span = spans.get(rec.get("entry", ""))
            if span is None:
                continue
            off, size = span
            rec["entry"] = group_name + "#" + str(off) + ":" + str(size) + ":" + str(count)


class _Precompressed:
    """把**已经压好**的字节喂给 zipfile ✅（绕开它自己的压缩 ✅）。

    zipfile 只认 STORED/DEFLATE ✅ → 我们要写 ZIP_ZSTANDARD ✅ → 只能「预先压好 ✅
    再喂进去 ✅」✅（替换 zipfile 的压缩器工厂 ✅ —— 私有 API ✅ 但工具链钉死 3.14 ✅）。
    """

    __slots__ = ("_blob", "_sent")

    def __init__(self, blob: bytes):
        self._blob = blob
        self._sent = False

    def compress(self, data: bytes) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._blob

    def flush(self) -> bytes:
        return b""


@contextlib.contextmanager
def _precompressed_writer(order: list[tuple[int, bytes]]):
    """让 zipfile 用我们**预先压好**的字节 ✅（顺序必须与写入顺序一致 ✅）。

    order ✅：按 entries 写出顺序排好的 (compress_type, blob) 序列 ✅
    （zstd 与 DEFLATE 统一走这里 ✅）—— zipfile 对每个非 STORED 条目调一次压缩器工厂 ✅
    → 队首类型吻合才弹出 ✅（STORED 条目不产生调用 ✅ 不弹 ✅）。"""
    original = zipfile._get_compressor
    queue: collections.deque = collections.deque(order)

    def _patched(compress_type, compresslevel=None):
        if queue and queue[0][0] == compress_type:
            return _Precompressed(queue.popleft()[1])
        return original(compress_type, compresslevel)

    zipfile._get_compressor = _patched
    try:
        yield
    finally:
        zipfile._get_compressor = original


def zstd_compress(data: bytes) -> bytes:
    """zstd 压缩 ✅（与 zipfile 内部用的同一个实现 ✅ → 字节一致 ✅ → 缓存可复用 ✅）。"""
    import compression.zstd as zstd

    return zstd.compress(data, ZSTD_LEVEL)


def _zstd_worker(task: tuple[str, bytes]) -> tuple[str, bytes]:
    """多进程 worker ✅（模块级函数 ✅ —— Windows 是 spawn ✅ → 必须可 pickle ✅）。"""
    name, data = task
    return name, zstd_compress(data)


def precompress_entries(entries: dict[str, bytes], cache_dir: Path, workers: int = 0, log=print) -> dict[str, bytes]:
    """把要 zstd 的条目**并行**压好 ✅（并按**内容哈希**走磁盘缓存 ✅）。

    为什么自己压 ✅：zipfile 只会**逐条顺序**压 ✅（实测 60 s ✅）→ ① 并行 ✅ ② 缓存 ✅
    都要「预先压好 ✅ 再喂给 zipfile ✅」✅。
    缓存口径 ✅：按**原始字节**的 sha256 ✅ → 内容没变 ✅ 直接命中 ✅（省掉整轮压缩 ✅）。
    """
    targets = [name for name in entries if entry_compress_type(name) == zipfile.ZIP_ZSTANDARD]
    if not targets:
        return {}
    # **zstd 缓存目录也不能是链接/junction** ✅：否则写到链接目标 ✅
    # （A203/A204/A206/A207/A208/A210/A211/A212/A213/A214/A215/A217 同类 ✅）。
    _require_plain_dir(cache_dir, "build/cratix/zstdcache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    # **压缩字节 sha 清单** ✅（A404）：命中校验从「zstd 解压比对」（~30 MB 解压 ✅
    # 实测 0.36 s/轮 ✅）换成「压缩字节的 sha256」（~1/6 成本 ✅）—— 防位腐能力等价 ✅：
    # 缓存字节任何变化（截断/位翻/换文件 ✅）都会让 sha 不匹配 ✅ → 重压 ✅（A148 同保护 ✅）。
    # 清单缺失/损坏 → 回退解压校验（缓存照用 ✅ 不整轮失效 ✅）并补记 ✅。
    _manifest_path = cache_dir / "manifest.json"
    _blob_shas: dict[str, str] = {}
    _manifest_dirty = False
    try:
        _saved = json.loads(_manifest_path.read_text(encoding="utf-8"))
        if isinstance(_saved, dict):
            _blob_shas = {str(k): str(v) for k, v in _saved.items()}
    except OSError, ValueError:
        _blob_shas = {}
    out: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    todo: list[tuple[str, bytes]] = []
    for name in targets:
        data = entries[name]
        # **缓存 key 要包含压缩级别** ✅：旧口径只对原始字节 sha256 ✅ →
        # 改 ZSTD_LEVEL 后仍复用旧级别的压缩产物 ✅（配置不生效 ✅）。
        digest = hashlib.sha256(data).hexdigest()
        # **zstd 实现随解释器版本变** ✅：同一原始字节 + 级别 ✅
        # 在不同 Python 下压缩字节可能不同 ✅ → 缓存要分开 ✅。
        digests[name] = f"{digest}.l{ZSTD_LEVEL}.p{sys.version_info[0]}.{sys.version_info[1]}"
        cached = cache_dir / f"{digests[name]}.zst"
        if cached.exists():
            # **缓存文件也可能是目录** ✅： read_bytes 会抛 IsADirectoryError ✅
            # → 清掉异常项后重压 ✅（A222/A223 同类 ✅）。
            try:
                blob = cached.read_bytes()
            except OSError:
                try:
                    if cached.is_dir() and not cached.is_symlink():
                        shutil.rmtree(cached, ignore_errors=True)
                    else:
                        cached.unlink(missing_ok=True)
                except OSError:
                    pass
                todo.append((name, data))
                continue
            # **缓存要校验与写入时一致** ✅：磁盘损坏/截断的 .zst ✅
            # 直接进载荷 ✅ → 运行期 zstd 解压失败 ✅；校验失败就重压 ✅。
            # sha 与清单不一致 / 清单缺项 → 清单缺项时回退「解压比对」✅（缓存照用 ✅）。
            _want_sha = _blob_shas.get(f"{digests[name]}.zst")
            _ok = False
            if _want_sha is not None:
                _ok = hashlib.sha256(blob).hexdigest() == _want_sha
            else:
                try:
                    import compression.zstd as _zstd

                    _ok = _zstd.decompress(blob) == data
                    _blob_shas[f"{digests[name]}.zst"] = hashlib.sha256(blob).hexdigest()
                    _manifest_dirty = True
                except Exception:
                    _ok = False
            if not _ok:
                try:
                    cached.unlink()
                except OSError:
                    pass
                todo.append((name, data))
                continue
            out[name] = blob
            # **命中刷新 mtime** ✅：下面的 30 天清理看 mtime ✅
            # → 不刷新的话 ✅ 每天都用的缓存也会在 30 天后被删 ✅（下次重压 ✅）。
            try:
                os.utime(cached, None)
            except OSError:
                pass
        else:
            todo.append((name, data))
    if todo:
        count = workers or min(8, (os.cpu_count() or 4) // 2)
        start = time.time()
        if count > 1 and len(todo) > 1:
            # **线程**而不是进程 ✅：zstd 的压缩**释放 GIL** ✅（实测 ✅ 8 线程 ≈ 3.7 倍 ✅）
            # → 不用 spawn ✅ 不用 pickle ✅ 也没有子进程递归导入主模块的坑 ✅ ✅。
            import concurrent.futures as futures

            with futures.ThreadPoolExecutor(count) as pool:
                for name, blob in pool.map(_zstd_worker, todo):
                    out[name] = blob
        else:
            for name, data in todo:
                out[name] = zstd_compress(data)
        for name, _data in todo:
            blob = out[name]
            path = cache_dir / f"{digests[name]}.zst"  # 名字已带 .l<level> ✅
            if not path.exists():
                # **缓存也原子写** ✅：悬空 symlink 不能被跟随 ✅（A226/A256 同类 ✅）。
                _write_atomic(path, blob)
            # **新压字节的 sha 记进清单** ✅：下次命中只做 sha 校验 ✅。
            _blob_sha = hashlib.sha256(blob).hexdigest()
            if _blob_shas.get(f"{digests[name]}.zst") != _blob_sha:
                _blob_shas[f"{digests[name]}.zst"] = _blob_sha
                _manifest_dirty = True
        log(
            f"[payload] 并行压缩 {len(todo)} 个条目（{count} 进程 ✅）：{time.time() - start:.1f} s ✅"
            f"（缓存命中 {len(targets) - len(todo)} ✅）"
        )
    if _manifest_dirty:
        # **清单原子写** ✅：half-written manifest 会被下次当成损坏 ✅ → 回退解压校验 ✅。
        try:
            _write_atomic(_manifest_path, json.dumps(_blob_shas, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        except OSError:
            pass
    # **清理陈旧 zstd 缓存** ✅：缓存按内容+级别命名 ✅ → 升级/改级别后旧文件永远不用 ✅
    # 但一直堆着 ✅（实测当前 78MB / 495 个 ✅）。→ 删除 30 天未使用的 ✅。
    try:
        _cutoff = time.time() - 30 * 86400
        for _p in cache_dir.glob("*.zst"):
            try:
                if _p.stat().st_mtime < _cutoff:
                    _p.unlink()
            except OSError:
                pass
    except OSError:
        pass
    return out


def deflate_raw(data: bytes) -> bytes:
    """**raw DEFLATE**（wbits=-15 ✅ 级别 DEFLATE_LEVEL ✅）✅。

    zipfile 的 DEFLATE 压缩器就是 `zlib.compressobj(level, zlib.DEFLATED, -15)` ✅
    （writestr 整块写 → 单次 compress + flush ✅）→ 这里同口径产出 ✅ → 字节一致 ✅
    → 可以绕开 zipfile 的压缩器工厂直接喂 ✅（_Precompressed ✅）。
    注意不能用 `zlib.compress(data, level)` ✅ —— 那是 zlib 包装（头 + adler ✅）✅。"""
    import zlib

    co = zlib.compressobj(DEFLATE_LEVEL, zlib.DEFLATED, -15)
    return co.compress(data) + co.flush()


def _deflate_worker(task: tuple[str, bytes]) -> tuple[str, bytes]:
    """线程池 worker ✅：zlib 压缩释放 GIL ✅（模块级函数 ✅ 与 _zstd_worker 同口径 ✅）。"""
    name, data = task
    return name, deflate_raw(data)


def precompress_deflate(entries: dict[str, bytes], cache_dir: Path, workers: int = 0, log=print) -> dict[str, bytes]:
    """把 DEFLATED 条目（.pyc ✅ ~865 个）**预先压好** ✅ 并按内容哈希走磁盘缓存 ✅。

    旧行为 ✅：zipfile 在 writestr 里逐条 zlib level 9 重压 ✅（profile 865 次 0.6s ✅）——
    载荷内容不变也每轮白压 ✅。

    缓存形态 ✅：**单文件容器 zip**（不是逐条目小文件 ✅）—— 865 个几 KB 的小文件每次
    miss 白付 ~6s（实测 200 个 ≈ 1.3s ✅ NTUSN/Defender 的小文件创建开销 ✅）；
    容器名带**级别 + Python 版本** ✅（zlib 实现随解释器变 ✅）：
    `deflate.d{level}.p{maj}.{min}.zip`，条目名 = **原始字节的 sha256** ✅。
    命中校验能解压且与源一致 ✅（容器位腐/截断 ✅ A148 同口径 ✅）；
    容器整体损坏 → 当空重建 ✅；内容集变化才重写容器 ✅（纯命中只刷 mtime ✅）。
    """
    targets = [name for name in entries if entry_compress_type(name) == zipfile.ZIP_DEFLATED]
    if not targets:
        return {}
    _require_plain_dir(cache_dir, "build/deflatecache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    container = cache_dir / f"deflate.d{DEFLATE_LEVEL}.p{sys.version_info[0]}.{sys.version_info[1]}.zip"
    stored: dict[str, bytes] = {}
    if container.is_file():
        try:
            with zipfile.ZipFile(container) as zf:
                for _digest in zf.namelist():
                    stored[_digest] = zf.read(_digest)
        except Exception:  # noqa: BLE001 — 容器损坏 ✅ → 当空重建 ✅
            stored = {}
    out: dict[str, bytes] = {}
    digest_by_name: dict[str, str] = {}
    todo: list[tuple[str, bytes]] = []
    for name in targets:
        data = entries[name]
        digest = hashlib.sha256(data).hexdigest()
        digest_by_name[name] = digest
        blob = stored.get(digest)
        if blob is not None:
            # **命中也要校验** ✅：容器内的位腐/截断字节 ✅
            # 直接进载荷 → zipimport 解码失败 ✅；校验失败重压 ✅（A148 同口径 ✅）。
            try:
                import zlib as _zlib

                if _zlib.decompress(blob, -15) != data:
                    raise ValueError("mismatch")
            except Exception:
                blob = None
        if blob is None:
            todo.append((name, data))
        else:
            out[name] = blob
    if todo:
        count = workers or min(8, (os.cpu_count() or 4) // 2)
        start = time.time()
        if count > 1 and len(todo) > 1:
            import concurrent.futures as futures

            with futures.ThreadPoolExecutor(count) as pool:
                for name, blob in pool.map(_deflate_worker, todo):
                    out[name] = blob
        else:
            for name, data in todo:
                out[name] = deflate_raw(data)
        log(
            f"[payload] DEFLATE 预压 {len(todo)} 个条目（{count} 线程 ✅）：{time.time() - start:.1f} s ✅"
            f"（缓存命中 {len(targets) - len(todo)} ✅）"
        )
    # **容器内容集管理** ✅：只保留本轮 targets 的 digest ✅（旧条目不堆积 ✅）；
    # 内容集没变就不重写 ✅（纯命中只刷 mtime ✅ —— 30 天清理看 mtime ✅ A117 同口径 ✅）。
    wanted: dict[str, bytes] = {}
    missing = False
    for name in targets:
        digest = digest_by_name[name]
        blob = out.get(name)
        if blob is None:
            missing = True
            break
        wanted[digest] = blob
    if not missing and (todo or set(stored) != set(wanted)):
        tmp = container.with_name(f"{container.name}.tmp.{os.getpid()}.{time.time_ns()}")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
                for digest in sorted(wanted):
                    zf.writestr(digest, wanted[digest], compress_type=zipfile.ZIP_STORED)
            os.replace(tmp, container)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    elif not missing:
        try:
            os.utime(container, None)
        except OSError:
            pass
    # **清理旧容器** ✅：级别/Python 变更后的旧容器 ✅ 30 天未使用删除 ✅。
    try:
        _cutoff = time.time() - 30 * 86400
        for _p in cache_dir.glob("deflate.d*.zip"):
            try:
                if _p.stat().st_mtime < _cutoff:
                    _p.unlink()
            except OSError:
                pass
    except OSError:
        pass
    return out


def write_payload(path: Path, entries: dict[str, bytes], manifest: dict, cache_dir: Path | None = None) -> Path:
    """写出 zip 载荷（策略见 entry_compress_type ✅）。

    zstd 条目**预先压好** ✅（并行 ✅ + 内容哈希缓存 ✅）→ 再喂给 zipfile ✅
    （替换它的压缩器工厂 ✅ —— zipfile 自己只认 STORED/DEFLATE ✅）。
    DEFLATED 条目（.pyc ✅）同口径预压 + 缓存 ✅（build/deflatecache ✅）。

    **固定时间戳** ✅：`writestr(str)` 给每个条目嵌**当前时间**的 DOS 时间戳 ✅
    （实测偏移 10 起字节漂移 ✅）→ exe 每次构建字节都不同 ✅ → 破坏**字节级可复现** ✅
    （/Brepro 只管 pyd 层 ✅ 载荷层漏了 ✅）。全条目用固定 ZipInfo ✅ →
    同输入 → 同字节 ✅（发布校验/审计/缓存完整性核对的前提 ✅）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # **先断链接** ✅：ZipFile("w") 会 truncate hardlink 目标 ✅
    # → 写到外部 ✅（A225/A226/A227 同类 ✅）。
    clear_leaf(path)
    # **载荷必须自带 entry.txt** ✅：stub 只认它 ✅ —— 缺失时产物启动即失败 ✅
    # （旧行为照样写出 zip ✅ → 构建报成功 ✅）。
    # **条目名必须是 zip 根相对 POSIX 路径** ✅：反斜杠/绝对路径/`..` 会变成
    # 不可 import 或越界的条目 ✅（旧行为照样写出 ✅）。
    for _name in entries:
        if (
            not _name
            or any(ord(_ch) < 32 for _ch in _name)
            or _name.endswith("/")
            or "\\" in _name
            or _name.startswith("/")
            or (len(_name) >= 2 and _name[1] == ":")
            or "" in _name.split("/")
            or "." in _name.split("/")
            or ".." in _name.split("/")
        ):
            raise SystemExit(f"[payload] 非法载荷条目名：{_name!r}")
        # **长度上限** ✅（A413）：stub 的 zipread 条目名缓冲是 `char name[300]` ✅
        # 超长条目在 zipidx_open 里被**静默截成空名** ✅（nlen >= sizeof(name) → name[0]=0 ✅）
        # → 运行期 import 报「找不到模块」且极难定位 ✅ → 构建期直接失败 ✅。
        if len(_name) >= 300:
            raise SystemExit(f"[payload] 载荷条目名超长（stub 缓冲上限 300）：{len(_name)} {_name!r}")
    if "entry.txt" not in entries:
        raise SystemExit("[payload] 载荷缺少 entry.txt（构建失败）")
    if "meta.json" in entries:
        raise SystemExit("[payload] entries 不应包含 meta.json（write_payload 会写）")
    if manifest.get("entry"):
        try:
            _entry_text = entries["entry.txt"].decode("utf-8", "replace").strip()
        except Exception:
            _entry_text = ""
        if _entry_text != manifest["entry"]:
            raise SystemExit(f"[payload] entry.txt 与 meta.entry 不一致：{_entry_text!r} != {manifest['entry']!r}")
    # **entry_count = 最终 zip 条目数** ✅：下面还会写一份 meta.json ✅
    # → 旧口径在 cli 里计的是 len(entries) ✅（少 1 ✅）。
    manifest["entry_count"] = len(entries) + 1
    _zdir = cache_dir or (path.parent / "zstdcache")
    pre = precompress_entries(entries, _zdir)
    # **DEFLATED 条目也预压 + 内容哈希缓存** ✅：~870 个 .pyc 逐条 zlib level 9 ✅
    # 内容不变也每轮白压（profile 0.6s ✅）→ 仿 zstdcache（sha256 + 级别 + Python ✅）。
    pre_d = precompress_deflate(entries, _zdir.parent / "deflatecache")
    # **写出顺序对齐** ✅：_precompressed_writer 的队列必须与 writestr 调用顺序一致 ✅
    # （zipfile 对每个非 STORED 条目调一次压缩器工厂 ✅）。
    _order: list[tuple[int, bytes]] = []
    for _name in entries:
        _ctype = entry_compress_type(_name)
        if _ctype == zipfile.ZIP_ZSTANDARD and _name in pre:
            _order.append((_ctype, pre[_name]))
        elif _ctype == zipfile.ZIP_DEFLATED and _name in pre_d:
            _order.append((_ctype, pre_d[_name]))
    fixed = (1980, 1, 1, 0, 0, 0)  # DOS 时间戳下限 ✅

    def _zinfo(name: str) -> zipfile.ZipInfo:
        return zipfile.ZipInfo(name, date_time=fixed)

    # **写临时文件再替换** ✅：写到一半失败不能留截断 payload ✅
    # （A228 已断链；这里再保证原子性 ✅）。
    _tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with zipfile.ZipFile(_tmp, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr(
                _zinfo("meta.json"),
                json.dumps(manifest, ensure_ascii=False, indent=2),
                compress_type=zipfile.ZIP_STORED,
            )
            with _precompressed_writer(_order):
                for name, blob in entries.items():
                    ctype = entry_compress_type(name)
                    zf.writestr(_zinfo(name), blob, compress_type=ctype, compresslevel=entry_compress_level(ctype))
        os.replace(_tmp, path)
    finally:
        try:
            _tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return path
