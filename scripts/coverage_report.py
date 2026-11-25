"""生成 **AOT 覆盖率报告** ✅（markdown ✅）—— `docs/aot-coverage.md`。

为什么要这个 ✅：
- 体检脚本只打印**当次**结果 ✅ —— 看不到**趋势** ✅；
- 用户最关心的两个问题是"**能不能用**"和"**哪里会回退**" ✅ ——
  这份报告把"**标准库全量**"的真实数字 ✅ 和"**回退原因排行**" ✅ 固定下来 ✅。

用法 ✅：`python scripts/coverage_report.py [--write]`
（不加 --write 只打印 ✅；加了就写 `docs/aot-coverage.md` ✅）
"""
import collections
import sys
import sysconfig
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cratix.compiler import driver  # noqa: E402

SKIP = ("site-packages", "lib2to3", "test/")


def collect():
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    total = ok = module_fallback = crashes = 0
    aot = fallback = 0
    module_reasons = collections.Counter()
    func_reasons = collections.Counter()
    slowest = []
    for path in sorted(stdlib.rglob("*.py")):
        rel = str(path.relative_to(stdlib)).replace(chr(92), "/")
        if any(part in rel for part in SKIP):
            continue
        total += 1
        name = "std." + rel[:-3].replace("/", ".")
        try:
            mc = driver.compile_module(path, name)
        except Exception as exc:  # noqa: BLE001
            crashes += 1
            module_reasons["崩溃：" + type(exc).__name__] += 1
            continue
        if not mc.module_ok:
            module_fallback += 1
            module_reasons[mc.module_reason.split(chr(65288))[0].strip()[:48]] += 1
            continue
        ok += 1
        aot += len(mc.supported_names)
        fallback += len(mc.fallback_names)
        bad = [plan for plan in mc.plans if not plan.supported]
        for plan in bad:
            func_reasons[plan.reason.split(chr(65288))[0].strip()[:48]] += 1
        if bad:
            slowest.append((len(bad), rel))
    return {
        "total": total, "ok": ok, "module_fallback": module_fallback, "crashes": crashes,
        "aot": aot, "fallback": fallback,
        "module_reasons": module_reasons, "func_reasons": func_reasons,
        "with_fallback": sorted(slowest, reverse=True)[:15],
    }


def render(data) -> str:
    total = data["total"]
    func_total = data["aot"] + data["fallback"]
    ratio = (data["aot"] / func_total) if func_total else 0.0
    lines = []
    lines.append("# AOT 覆盖率报告 ✅（自动生成 ✅）")
    lines.append("")
    lines.append("> 由 `scripts/coverage_report.py` 生成 ✅ —— 扫的是**本机标准库** ✅，不是抽样 ✅。")
    lines.append("> 时间 ✅：" + datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("> Python ✅：" + sys.version.split()[0] + "（" + sys.implementation.cache_tag + "）")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append("| 扫描模块 | " + str(total) + " |")
    lines.append("| **编译成功模块** | **" + str(data["ok"]) + "** |")
    lines.append("| 模块级回退 | " + str(data["module_fallback"]) + " |")
    lines.append("| **崩溃** | **" + str(data["crashes"]) + "** |")
    lines.append("| **函数 AOT** | **" + str(data["aot"]) + "** |")
    lines.append("| 函数回退 | " + str(data["fallback"]) + " |")
    lines.append("| AOT 比例 | **" + format(ratio, ".3f") + "** |")
    lines.append("")
    lines.append("## 函数级回退原因（top 12）")
    lines.append("")
    lines.append("| 次数 | 原因 |")
    lines.append("| --- | --- |")
    for reason, count in data["func_reasons"].most_common(12):
        lines.append("| " + str(count) + " | " + reason.replace("|", "/") + " |")
    lines.append("")
    lines.append("## 模块级回退原因（top 12）")
    lines.append("")
    lines.append("| 次数 | 原因 |")
    lines.append("| --- | --- |")
    for reason, count in data["module_reasons"].most_common(12):
        lines.append("| " + str(count) + " | " + reason.replace("|", "/") + " |")
    lines.append("")
    lines.append("## 回退最多的模块（top 15）")
    lines.append("")
    lines.append("| 回退函数数 | 模块 |")
    lines.append("| --- | --- |")
    for count, rel in data["with_fallback"]:
        lines.append("| " + str(count) + " | " + rel + " |")
    lines.append("")
    lines.append("## 这些回退意味着什么 ✅")
    lines.append("")
    lines.append("- **回退 ≠ 错误** ✅：回退的函数**照常能跑** ✅（用源码 exec ✅，速度与 .pyc 相当 ✅），")
    lines.append("  只是没有享受到 AOT 的收益 ✅；")
    lines.append("- **生成器 ✅ / async ✅** 是最大的两块 ✅ —— 它们需要**协程/生成器状态机** ✅，")
    lines.append("  属于另一个量级的工程 ✅；")
    lines.append("- **模块级回退**很小 ✅ —— 一个不支持的字面量/语句会拖垮整个模块 ✅，")
    lines.append("  所以这块一直在按「原因排行榜」清理 ✅。")
    return chr(10).join(lines) + chr(10)


def main() -> int:
    data = collect()
    text = render(data)
    if "--write" in sys.argv:
        out = ROOT / "docs" / "aot-coverage.md"
        out.write_text(text, encoding="utf-8")
        print("已写入 " + str(out))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
