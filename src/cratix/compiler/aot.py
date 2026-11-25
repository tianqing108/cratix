"""AOT 编译编排：把一批项目模块编成 .pyd ✅。

返回三样东西 ✅：
    entries    载荷条目（ext/<stem>.pyd ✅）；
    records    meta.json 的 extensions 记录 ✅（引导模块据此从内存加载 ✅）；
    failures   每个模块的失败原因 ✅（写进 report.json ✅）。

**模块级支持范围**（v1 ✅）：常量赋值 ✅ + import ✅ + 函数 ✅；
其余（类 ✅ / from-import ✅ / 其它语句 ✅）→ **整个模块回退 .pyc** ✅。
"""

from __future__ import annotations

from pathlib import Path

from . import driver

PAYLOAD_PREFIX = "ext/"


def compile_modules(modules, out_dir: Path, python_prefix: Path | None = None):
    """编译一批 (模块名, 源文件) ✅。

    modules: list[tuple[str, Path]] —— 模块名是点号形式 ✅（如 hello.app ✅）。
    """
    entries: dict[str, bytes] = {}
    records: list[dict] = []
    failures: dict[str, str] = {}
    compiled: list[str] = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for module_name, source in modules:
        try:
            mc = driver.compile_module(Path(source), module_name)
        except Exception as exc:  # noqa: BLE001 - 编译失败要回退 ✅ 不能中断构建 ✅
            failures[module_name] = "前端失败：" + str(exc)
            continue
        if not mc.module_ok:
            failures[module_name] = mc.module_reason
            continue
        if not mc.supported_names:
            failures[module_name] = "没有可 AOT 化的函数"
            continue
        try:
            pyd = driver.build_pyd(mc, out_dir, python_prefix=python_prefix)
        except Exception as exc:  # noqa: BLE001 - 编译失败要回退 ✅
            failures[module_name] = "C 编译失败：" + str(exc)[:200]
            continue
        stem = mc.init_symbol[len("PyInit_"):]
        entry = PAYLOAD_PREFIX + stem + ".pyd"
        entries[entry] = pyd.read_bytes()
        rel = Path(source).name
        records.append(
            {
                "module": module_name,
                "entry": entry,
                "init": mc.init_symbol,
                "kind": "module",
                "virtual": "<exe>/" + module_name.replace(".", "/") + ".py",
                "virtual_dir": "<exe>/" + module_name.replace(".", "/").rsplit("/", 1)[0],
                # **模块体已在加载时执行** ✅（多阶段 ✅ → FromDefAndSpec2 调了 exec 槽位 ✅）
                # → 引导模块**不要**再调 exec_def ✅（否则会重跑一遍 ✅）
                "exec_done": True,
                "aot": {
                    "functions": sorted(mc.supported_names),
                    "fallback_functions": sorted(mc.fallback_names),
                },
            }
        )
        compiled.append(module_name)

    return entries, records, failures, sorted(compiled)