"""cratix 的 vendor 同步脚本：从 cypack 拷贝打包半边并应用统一改名与去业务化改写。

用法：
    python scripts/vendor_sync.py [--src C:/project/sports-server/tools/cypack]

设计要点：
    1. 可重复执行（幂等）：每次从上游重新拷贝，覆盖本地拷贝结果；
    2. **stub/stub.c 保留本地版本** ✅ —— 它是 cratix 改动最多的文件 ✅
       （D3 引导双路径 ✅ / D9 版本无关的解释器查找 ✅）——覆盖会冲掉本地改动 ✅；
    3. 改名与去业务化改写集中在本文件（RENAMES / POST_TRANSFORMS）✅ 便于审计；
    4. 只拷贝"打包半边"，不含 cypack 的编译/探针子系统（那部分由 cratix 自研替代）；
    5. 保留 LZMA SDK（公有领域；stub.c 链接需要），仅剔除缓存目录；
    6. 同步后**校验**自有 stub.c 的改造标记还在 ✅（上游漂移要暴露 ✅）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 上游 Python 模块 -> 本地目标（相对 src/cratix）
PY_FILES = {
    "deps.py": "packaging/deps.py",
    "pe.py": "packaging/pe.py",
    "payload.py": "packaging/payload.py",
    "assemble.py": "packaging/assemble.py",
    "stubbuild.py": "packaging/stubbuild.py",
    "manifest.py": "packaging/manifest.py",
    "native.py": "packaging/native.py",
    "trims.py": "packaging/upstream_trims.py",
    # verify_no_console.py / verify_zero_disk.py **不 vendor**：
    # 它们是独立脚本（顶层代码 ✅），cratix 需要的是**可导入的函数** ✅
    # → 重写为 src/cratix/packaging/verify.py ✅（含 CLI 接线 ✅）。
}

# 需要剔除的上游路径（相对上游根，POSIX）✅
# 注意：stub/xz（LZMA SDK）**必须保留** —— 它是公有领域（7-Zip SDK 的 LZMA 部分由
# Igor Pavlov 置于公有领域），且 stub.c 无条件引用 LzmaDec_* 符号，剔除会导致编译/链接失败。
EXCLUDE = {
    # **stub/stub.c 不覆盖** ✅：它是 cratix 改动最多的文件 ✅（D3 引导双路径 ✅ /
    # D9 版本无关的解释器查找 ✅）—— 每次同步覆盖会把本地改动冲掉 ✅（实测踩过 ✅）。
    "stub/stub.c",
    "stub/__pycache__",
    "__pycache__",
}

# 统一改名（大小写敏感，逐条替换；顺序有意义）
RENAMES = [
    ("CYPK1", "CRTX1"),
    ("CYPACK", "CRATIX"),
    ("cypack_bootstrap", "cratix_bootstrap"),
    ("cypack_loader", "cratix_loader"),
    ("cypack_smoke", "cratix_smoke"),
    ("cypack_gui_smoke", "cratix_gui_smoke"),
    ("cypack", "cratix"),
]

# 去业务化改写：(目标相对路径, 正则, 替换) —— 在改名之后应用
POST_TRANSFORMS = [
    ("packaging/payload.py", r"from \. import pe, trims", "from . import defaults, pe"),
    ("packaging/payload.py", r"\btrims\.", "defaults."),
    ("packaging/native.py", r"from \.trims import", "from .defaults import"),
    (
        "packaging/payload.py",
        r"PROJECT_TOPS: frozenset\[str\] = frozenset\(\s*\{[^}]*\}\s*\)",
        "PROJECT_TOPS = defaults.PROJECT_TOPS  # 配置驱动（见 defaults.py）",
    ),
    (
        "packaging/payload.py",
        r"PYC_PIPELINE_VERSION = \"[^\"]*\"",
        "PYC_PIPELINE_VERSION = \"cratix-1\"",
    ),
    (
        "packaging/stubbuild.py",
        r"STUB_DIR = Path\(__file__\)\.resolve\(\)\.parent / \"stub\"",
        "STUB_DIR = Path(__file__).resolve().parent.parent / \"stub\"",
    ),
    (
        "packaging/native.py",
        r"docs/archive/cython-single-file-packaging-plan\.md",
        "docs/PLAN.md",
    ),
]

# cratix 自有的 stub.c 必须保留的改造标记 ✅（缺一个就说明本地改动被覆盖了 ✅）
STUB_C_REQUIRED = (
    "find_interpreter_entry",
    "bootstrap ok（pyc 兜底路径）",
    "cratix_loader",
)
# 必须**不存在**的上游标识 ✅（改名必须生效 ✅）
STUB_C_FORBIDDEN = ("cypack_loader", "cypack_bootstrap", "CYPK1")

# 二进制/不可改名的后缀
BINARY_SUFFIXES = {".png", ".ico", ".dll", ".exe", ".lib", ".pyd", ".zip", ".zst"}


def apply_renames(text):
    for old, new in RENAMES:
        text = text.replace(old, new)
    return text


def apply_post_transforms(rel, text):
    for target_rel, pattern, repl in POST_TRANSFORMS:
        if rel == target_rel:
            text = re.sub(pattern, repl, text)
    return text


def _is_excluded(root_rel):
    return any(root_rel == e or root_rel.startswith(e + "/") for e in EXCLUDE)


def copy_tree(src, dst, src_root):
    """拷贝目录；EXCLUDE 命中的文件**保留本地版本** ✅。返回处理的文件数。"""
    selected = []
    excluded_rel = []
    for item in sorted(src.rglob("*")):
        root_rel = item.relative_to(src_root).as_posix()
        if _is_excluded(root_rel):
            if item.is_file():
                excluded_rel.append(item.relative_to(src).as_posix())
            continue
        if item.is_dir() or "__pycache__" in item.relative_to(src).as_posix():
            continue
        selected.append(item)
    # **先保留被排除文件的本地版本** ✅（cratix 自有改造 ✅）
    preserved = {}
    for rel in excluded_rel:
        local = dst / rel
        if local.is_file():
            preserved[rel] = local.read_bytes()
    # 清空目标（保证幂等 ✅）
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in selected:
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix.lower() in BINARY_SUFFIXES:
            shutil.copy2(item, target)
            count += 1
            continue
        raw = item.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            shutil.copy2(item, target)
            count += 1
            continue
        target.write_text(apply_renames(text), encoding="utf-8", newline="")
        count += 1
    # 恢复保留项 ✅
    for rel, data in preserved.items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return count


def _check_stub_c(pkg):
    """校验我们自有的 stub.c 仍带 cratix 改造（防止同步时本地改动被冲掉 ✅）。"""
    path = pkg / "stub" / "stub.c"
    if not path.is_file():
        raise SystemExit("[vendor] 缺少 src/cratix/stub/stub.c（cratix 自有文件 ✅）")
    text = path.read_text(encoding="utf-8")
    missing = [m for m in STUB_C_REQUIRED if m not in text]
    if missing:
        raise SystemExit("[vendor] stub.c 缺少改造标记：" + ", ".join(missing))
    leftover = [m for m in STUB_C_FORBIDDEN if m in text]
    if leftover:
        raise SystemExit("[vendor] stub.c 仍有上游标识（改名未生效 ✅）：" + ", ".join(leftover))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vendor_sync")
    parser.add_argument("--src", default="C:/project/sports-server/tools/cypack")
    args = parser.parse_args(argv)
    src = Path(args.src)
    if not src.is_dir():
        print("[vendor] 上游目录不存在: %s" % src, file=sys.stderr)
        return 1
    pkg = REPO / "src" / "cratix"
    (pkg / "packaging").mkdir(parents=True, exist_ok=True)
    total = 0
    for name, rel in PY_FILES.items():
        source = src / name
        if not source.is_file():
            print("[vendor] 跳过（上游缺失）: %s" % name, file=sys.stderr)
            continue
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = apply_renames(source.read_text(encoding="utf-8"))
        text = apply_post_transforms(rel, text)
        target.write_text(text, encoding="utf-8", newline="")
        total += 1
        print("[vendor] %s -> src/cratix/%s" % (name, rel))
    stub_src = src / "stub"
    if stub_src.is_dir():
        n = copy_tree(stub_src, pkg / "stub", src)
        total += n
        print("[vendor] stub/** -> src/cratix/stub/  (%d 个文件)" % n)
    _check_stub_c(pkg)
    print("[vendor] done, %d files processed" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())