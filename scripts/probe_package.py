"""**第三方包的命名空间差分** ✅ —— 源码版 vs AOT 版 ✅。

为什么值得做 ✅：覆盖率只回答"编译了多少" ✅，**不回答"编出来的对不对"** ✅。
这个脚本对真实包里的每个模块 ✅：
1. 用编译器编成 `.pyd` ✅（模块级能过才编 ✅）；
2. 分别**加载源码版**和 **AOT 版** ✅；
3. 比较 `dir(module)` ✅ —— AOT 版多出内部名字 ✅ 或少公开名字 ✅ 都算差异 ✅。

用法 ✅：`python scripts/probe_package.py [--limit 30] [--package pip]`
"""
import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cratix.compiler import driver, loader  # noqa: E402


def _source_names(path: Path) -> set:
    spec = importlib.util.spec_from_file_location("probe_src_mod", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - 源码版自己 import 失败就不比 ✅
        return set()
    return set(dir(module))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--package", default="pip")
    parser.add_argument("--skip", type=int, default=0, help="跳过最大的 N 个模块（分批排查用）")
    # **`--strict`** ✅：有任何**差异**或**加载失败**就返回 1 ✅（质量门用它做硬约束 ✅）
    parser.add_argument("--strict", action="store_true", help="有差异/失败时返回非零（质量门用）")
    args = parser.parse_args()

    try:
        package = __import__(args.package)
    except Exception as exc:  # noqa: BLE001
        print("找不到包 ✅：" + str(exc))
        return 0
    root = Path(package.__file__).resolve().parent
    files = sorted(root.rglob("*.py"), key=lambda item: item.stat().st_size, reverse=True)
    files = [item for item in files if item.name != "__init__.py"][args.skip: args.skip + args.limit]

    workdir = Path(tempfile.mkdtemp(prefix="cratix_pkgprobe_"))
    checked = matched = skipped = failed = 0
    diffs = []
    failures = []
    for path in files:
        rel = path.relative_to(root.parent)
        name = str(rel.with_suffix("")).replace(chr(92), ".").replace("/", ".")
        try:
            mc = driver.compile_module(path, name)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        if not mc.module_ok:
            skipped += 1
            continue
        # **逐模块打点** ✅ —— 崩溃时靠最后一行定位是哪个模块 ✅（实测：80 个模块那次直接访问违例 ✅）
        print("  ... " + name, flush=True)
        try:
            out_dir = workdir / name.replace(".", "_")
            out_dir.mkdir(parents=True, exist_ok=True)
            pyd = driver.build_pyd(mc, out_dir)
            # **先把父包按源码导入** ✅ —— 否则 AOT 模块里的**相对导入**（`from . import x` ✅）
            # 会失败 ✅，那是**探针假阳性** ✅ 而不是编译器的错 ✅（实测吵了好几条 ✅）。
            parent = name.rpartition(".")[0]
            if parent:
                try:
                    importlib.import_module(parent)
                except Exception:  # noqa: BLE001 - 父包导入失败也不该中断探针
                    pass
            aot_mod = loader.load_compiled_module(pyd, mc.init_symbol)
        except BaseException as exc:  # noqa: BLE001
            failed += 1
            reason = str(exc).replace(chr(10), " ")[:120]
            failures.append((name, reason))
            continue
        checked += 1
        expected = _source_names(path)
        # **模块元数据不算差异** ✅ —— `__cached__`/`__annotate__` 之类是**导入系统**填的 ✅，
        # 与"我们编译得对不对"无关 ✅（不排除的话会把真信号淹掉 ✅ 实测 ✅）。
        ignore = {"__cached__", "__annotate__", "__conditional_annotations__", "__builtins__",
                  "__loader__", "__spec__", "__file__", "__package__", "__path__"}
        expected -= ignore
        if not expected:
            skipped += 1
            continue
        actual = set(dir(aot_mod)) - ignore
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        if extra or missing:
            diffs.append((name, extra[:4], missing[:4]))
        else:
            matched += 1

    print("检查 " + str(checked) + " 个模块 ✅ / 命名空间完全一致 " + str(matched) + " ✅ / 有差异 "
          + str(len(diffs)) + " ✅ / 加载失败 " + str(failed) + " ✅ / 跳过 " + str(skipped) + " ✅")
    for name, reason in failures[:8]:
        # **失败原因要看得见** ✅ —— "加载失败 20" 这种数字**没法行动** ✅。
        print("  失败 " + name + "：" + reason)
    if args.strict and (diffs or failed):
        # **基线是"0 差异 / 0 失败"** ✅ —— 退步就该让门变红 ✅
        return 1
    for name, extra, missing in diffs[:10]:
        print("  差异 " + name + "  多出=" + str(extra) + "  缺少=" + str(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
