"""编译 stub（纯 C，MSVC）。

stub 不链接 python314.lib —— 否则 OS 会在进程启动时从磁盘加载 python314.dll，
与「解释器也放进内存」的目标冲突。所有 Python API 都在运行时从已映射模块的导出表解析。
"""

from __future__ import annotations

import os
import subprocess
import time
from functools import lru_cache
import tempfile
from pathlib import Path

STUB_DIR = Path(__file__).resolve().parent.parent / "stub"


@lru_cache(maxsize=1)
def find_vcvars() -> Path:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if vswhere.exists():
        proc = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        inst = proc.stdout.strip()
        if inst:
            candidate = Path(inst) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
            if candidate.exists():
                return candidate
    for base in (
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ):
        if not base.exists():
            continue
        for ver in sorted(base.glob("*/")):
            for edition in ("Community", "Professional", "Enterprise", "BuildTools"):
                candidate = ver / edition / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
                if candidate.exists():
                    return candidate
    raise SystemExit("找不到 vcvars64.bat（需要安装 MSVC 工具链）")


def vcvars_identity() -> str:
    """MSVC 工具链身份：vcvars 路径 + 每个工具集的 cl.exe mtime/size。

    旧口径只把 vcvars 绝对路径写进指纹；VS **就地升级**时路径不变 →
    旧 stub/obj/pyd 仍被复用（A187/A188/A189 的「升级 VS 不复用」只对
    换安装路径成立）。
    """
    v = find_vcvars()
    parts = [f"vcvars={v}"]
    # **浅路径不能炸**：find_vcvars 若返回自定义/相对路径（测试/未来布局），
    # 旧写法 parents[2] 直接 IndexError → 整个构建崩。
    tools = v.parents[2] / "Tools" / "MSVC" if len(v.parents) > 2 else None
    if tools is not None and tools.is_dir():
        for cl in sorted(tools.glob("*/bin/Hostx64/x64/cl.exe")):
            try:
                st = cl.stat()
            except OSError:
                continue
            parts.append(f"cl={cl.parents[3].name}|{st.st_mtime_ns}|{st.st_size}")
    return "|".join(parts)


def run_cmd(cmd: str, cwd: Path | None = None) -> str:
    """把命令写进临时 .bat 再执行。

    直接 subprocess(["cmd", "/c", cmd]) 会让 list2cmdline 把内部引号转义成 \"，
    cmd 于是把带引号的路径当成字面量 —— 必须走 .bat 才拿到 cmd 原生的引号语义。

    **bat 名带 pid 唯一化** ✅：固定名（cratix_build.bat）会让**并发构建**互踩 ✅
    （同一个仓库同时两个构建 ✅ 甚至**两个不同仓库**都用 cratix ✅ → 互相覆盖对方的命令 ✅
    → 对方构建跑错命令 ✅）。bat 用完即删 ✅。
    """
    # **临时 bat 名字带 ns 且独占创建** ✅：固定名（仅 pid）可被预置 hardlink/symlink ✅
    # → write_text 会截断外部目标 ✅（A226/A258/A259 同类 ✅）。
    bat = Path(tempfile.gettempdir()) / f"cratix_build_{os.getpid()}_{time.time_ns()}.bat"
    try:
        with open(bat, "xb") as _h:
            _h.write(("@echo off\r\nchcp 65001 >nul\r\n" + cmd + "\r\n").encode("utf-8"))
        proc = subprocess.run(
            ["cmd", "/c", str(bat)],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        try:
            bat.unlink(missing_ok=True)
        except OSError:
            pass
    if proc.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {proc.returncode}）:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def _marker_ok(marker: Path, want: str) -> bool:
    try:
        return marker.is_file() and marker.read_text(encoding="utf-8", errors="replace") == want
    except OSError:
        return False


def _atomic_write_text(path: Path, text: str) -> None:
    """**rc 脚本/中间文本原子写** ✅：预置 hardlink/symlink 会让 write_text 写外部 ✅
    （A225-A262 同类 ✅）。"""
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
        import shutil as _sh

        _sh.rmtree(path, ignore_errors=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with open(tmp, "xb") as _h:
            _h.write(text.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def build_stub(build_dir: Path, console: bool = False, icon: Path | None = None) -> Path:
    """编译 stub.exe（含 memdl / zipread / vfs）。

    console=True ✅ → **控制台子系统** ✅（调试用 ✅ —— 弹控制台窗口 ✅）；
    默认 ✅ → **GUI 子系统** ✅（/ENTRY:mainCRTStartup ✅ —— 入口仍是 main ✅）→ **不弹窗口** ✅。
    icon ✅ → 用 rc 把 .ico 编成资源 ✅ 再链进去 ✅（exe 图标 ✅）。
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    build_dir = build_dir.resolve()  # **必须绝对路径** ✅ —— 命令里会 cd 进 build_dir ✅（相对路径会指错 ✅）
    if icon is not None:
        icon = icon.resolve()
    vcvars = find_vcvars()
    # LZMA SDK + libzstd 解码器静态链接 ✅（ZIP_LZMA / ZIP_ZSTANDARD 条目解码 ✅ M7 ✅ 无 DLL 依赖 ✅）
    # zstd 用官方 combine.sh 合并出的**单文件** ✅（zstd_all.c ✅ —— 分散编译的 include
    # 顺序与 Windows ERROR 宏打架 ✅ 合并成一个 TU 就没有这问题 ✅）。
    lzma_dir = STUB_DIR / "xz"
    zstd_dir = STUB_DIR / "zstd"
    sources = [STUB_DIR / name for name in ("stub.c", "memdl.c", "zipread.c", "vfs.c", "tlsfix.c", "diag.c")]
    if (lzma_dir / "LzmaDec.c").is_file():
        sources.append(lzma_dir / "LzmaDec.c")
    if (zstd_dir / "zstd_all.c").is_file():
        # xxhash.c **单独编** ✅：合并进 zstd_all.c 时 XXH_IMPLEMENTATION 的
        # include-guard 时序会让实现丢失 ✅（链接报 XXH64_reset 未解析 ✅）
        sources.append(zstd_dir / "common" / "xxhash.c")
        sources.append(zstd_dir / "zstd_all.c")
    # 静态 TLS 修补的「取表」助手 ✅ —— **必须用 MASM** ✅（要保留除 rax 外的全部寄存器 ✅）
    asm_src = STUB_DIR / "tlsfix.asm"
    # **obj 名字必须与 cl 的产物不同名** ✅ —— cl 编译 tlsfix.c 也产出 tlsfix.obj ✅
    # → 会把 MASM 的 obj 覆盖掉 ✅ → 链接报「未解析符号」✅（实测 ✅）
    asm_obj = build_dir / "tlsfix_asm.obj"
    # **子系统必须走 /link 传给链接器** ✅ —— 直接给 cl 的 /SUBSYSTEM 会被 cl 覆盖成 CONSOLE ✅
    # （实测 ✅：cl 侧写 /SUBSYSTEM:WINDOWS → 产物仍是 "Windows CUI" ✅；走 /link 才是 "Windows GUI" ✅）
    # **/Brepro 必须给** ✅：否则 PE 的 TimeDateStamp 每次链接都不同 ✅（实测：同源码两次构建
    # 仅 2 处字节不同 ✅ 都是 4 字节时间戳 ✅）→ stub.exe 字节变 ✅ → 最终 exe 的 sha256
    # 每次重建都变 ✅（与 docs/cratix-build.md「同代码连续两次构建 sha256 一致」矛盾 ✅）。
    subsystem = (
        "/link /Brepro /SUBSYSTEM:CONSOLE" if console else "/link /Brepro /SUBSYSTEM:WINDOWS /ENTRY:mainCRTStartup"
    )
    res = build_dir / "app.res"
    rc_src = build_dir / "app.rc"
    segs: list[str] = []
    if icon is not None and icon.is_file():
        # rc.exe **不吃 .ico** ✅ —— 得先写一段 .rc 脚本 ✅ 再编译成 .res ✅
        # 路径用正斜杠 ✅ —— .rc 里反斜杠是转义符 ✅
        _atomic_write_text(rc_src, f'1 ICON "{icon.as_posix()}"\n')
        segs.append(f'rc /fo"{res}" "{rc_src}"')
    link_sources = " ".join(f'"{s}"' for s in sources)
    if asm_src.is_file():
        segs.append(f'ml64 /nologo /c /Fo"{asm_obj}" "{asm_src}"')
        link_sources += f' "{asm_obj}"'
    if icon is not None and icon.is_file():
        link_sources += f' "{res}"'
    # **/link 必须放在最后** ✅ —— 它后面的参数全部交给链接器 ✅
    segs.append(f"cl /nologo /O2 /MT /W3 /utf-8 /Fe:stub.exe {link_sources} {subsystem}")
    cmd = f'call "{vcvars}" >nul && cd /d "{build_dir}" && ' + " && ".join(segs)
    exe = build_dir / "stub.exe"
    marker = build_dir / "stub.kind"
    kind = "console" if console else "gui"
    # **增量** ✅：stub 源码没变 ✅ 就复用上次的 stub.exe ✅ —— 否则每次构建都要跑一遍
    # MSVC ✅（6 个 C 文件 + MASM + 链接 ✅ ≈ 8 s ✅）。子系统变了 ✅ → 必须重编 ✅。
    #
    # 指纹必须覆盖**全部构建输入** ✅ —— 旧口径只看 6 个 .c + .asm 的 mtime ✅，两个真 bug：
    #   ① **头文件改动不重编** ✅：memdl.h / vfs.h / zipread.h 改了 ✅ → stub.exe 的 mtime
    #      仍比 6 个 .c 新 ✅ → 复用旧 stub ✅ → 产物里是**旧头文件语义** ✅（实测 ✅）；
    #   ② **源文件增删不重编** ✅：删掉 zstd_all.c ✅ → newest 反而变小 ✅ → 复用**带 zstd**
    #      的旧 exe ✅；反过来新增源文件（mtime 比 exe 旧，比如从归档解出来）也照样复用 ✅。
    # → 改成「相对路径 + mtime_ns + size」全量指纹（.c / .h / .asm 递归）+ 子系统 + 图标 ✅。
    #   任何增删改都必重编 ✅；旧格式 marker（只有 "gui"/"console"）不匹配 → 重建一次 ✅。
    inputs = sorted(p for p in STUB_DIR.rglob("*") if p.is_file() and p.suffix.lower() in {".c", ".h", ".asm"})
    newest = max([p.stat().st_mtime for p in inputs] or [0.0])
    # **工具链也进指纹** ✅：旧口径只看源文件 ✅ → 升级 VS/MSVC 后会复用
    # 旧编译器链出的 stub ✅（与 A87 同类 ✅）。把 vcvars 路径与固定编译 flags 也算进去 ✅。
    fingerprint = [f"kind={kind}", vcvars_identity(), "cl=/O2 /MT /W3 /utf-8", "ml64=1"]
    import hashlib as _src_hash

    for p in inputs:
        st = p.stat()
        # **源码内容也要进指纹** ✅：旧口径只看 mtime+size ✅
        # → 同尺寸修改且 mtime 被保留的源码会复用旧 stub ✅。
        src_sha = _src_hash.sha256(p.read_bytes()).hexdigest()
        fingerprint.append(f"{p.relative_to(STUB_DIR).as_posix()}|{st.st_mtime_ns}|{st.st_size}|{src_sha}")
    if icon is not None and icon.is_file():
        st = icon.stat()
        # **图标内容也要进指纹** ✅：旧口径只看 mtime+size ✅
        # → 同尺寸替换图标且 mtime 被保留 ✅ → 旧 stub 被复用 ✅。
        import hashlib as _hashlib

        icon_sha = _hashlib.sha256(icon.read_bytes()).hexdigest()
        fingerprint.append(f"icon|{st.st_mtime_ns}|{st.st_size}|{icon_sha}")
        newest = max(newest, st.st_mtime)
    want = "\n".join(fingerprint)
    usable = False
    if exe.exists():
        try:
            data = exe.read_bytes()
            usable = len(data) > 0 and data[:2] == b"MZ"
        except OSError:
            usable = False
    if (
        usable
        and _marker_ok(marker, want)
        and exe.stat().st_mtime >= newest
    ):
        return exe
    # **先断链接** ✅：cl /Fe 会 truncate hardlink 目标 ✅
    # （A225/A226/A227/A228 同类 ✅）。
    try:
        if exe.is_symlink() or (hasattr(exe, "is_junction") and exe.is_junction()):
            # **联接/符号链接只删链接本身** ✅：rmtree 对 junction 是 no-op ✅
            # （实测：rmtree(junction, ignore_errors=True) 后链接仍在 ✓ → cl /Fe 写目录失败 ✓）。
            if exe.is_dir():
                exe.rmdir()
            else:
                exe.unlink()
        elif exe.is_dir():
            import shutil as _sh

            _sh.rmtree(exe, ignore_errors=True)
        else:
            exe.unlink(missing_ok=True)
    except OSError:
        pass
    run_cmd(cmd)
    if not exe.exists():
        raise SystemExit("stub.exe 未生成")
    # **marker 也走统一原子写** ✅：直接 write_text 会跟随 hardlink 写外部 ✅
    # （A225-A230 同类 ✅；junction 由 helper 统一处理 ✅）。
    _atomic_write_text(marker, want)
    return exe
