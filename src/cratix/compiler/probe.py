"""双进程差分探针 ✅。

**两个独立子进程** ✅：一个加载源码版 ✅ 一个加载 AOT 版 ✅ → 各自输出 JSON ✅ →
父进程比对 ✅。不一致 → 该模块**回退 .pyc** ✅（构建仍成功 ✅）。

比对的只有**稳定语义** ✅：模块级名字 ✅ / 函数元数据 ✅ / 模块级常量值 ✅。
不比对实现细节 ✅（字节码 ✅ / 内存地址 ✅ / 异常消息 ✅）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORKER = "cratix.compiler.probe_worker"
PROBE_TIMEOUT = 120


def _run(spec: dict) -> dict:
    """跑一次观测 ✅（子进程 ✅）。"""
    payload = json.dumps(spec)
    env = dict(os.environ)
    # 子进程要能 import cratix ✅ —— 继承父进程的 PYTHONPATH ✅ 并补上 src ✅
    src_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", WORKER, payload],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=PROBE_TIMEOUT,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()[-400:]}
    try:
        return json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "探针输出不是 JSON：" + str(exc)}

def compare(source: dict, aot: dict) -> list[str]:
    """比对两次观测 ✅ —— 返回差异描述列表 ✅（空 = 一致 ✅）。"""
    diffs: list[str] = []
    if not source.get("ok"):
        return ["源码版观测失败：" + str(source.get("error", ""))]
    if not aot.get("ok"):
        return ["AOT 版观测失败：" + str(aot.get("error", ""))]
    src_names = set(source.get("names", []))
    aot_names = set(aot.get("names", []))
    for name in sorted(src_names - aot_names):
        diffs.append("AOT 版缺少名字：" + name)
    for name in sorted(aot_names - src_names):
        diffs.append("AOT 版多出名字：" + name)
    src_fns = source.get("functions", {})
    aot_fns = aot.get("functions", {})
    for name in sorted(set(src_fns) & set(aot_fns)):
        s = src_fns[name]
        a = aot_fns[name]
        if s.get("name") != a.get("name"):
            diffs.append(name + " 的 __name__ 不同：" + str(s.get("name")) + " vs " + str(a.get("name")))
        if s.get("params") is not None and a.get("params") is not None:
            sp = [(p["name"], p["kind"], p["has_default"]) for p in s["params"]]
            ap = [(p["name"], p["kind"], p["has_default"]) for p in a["params"]]
            if sp != ap:
                diffs.append(name + " 的参数签名不同：" + str(sp) + " vs " + str(ap))
    src_const = source.get("constants", {})
    aot_const = aot.get("constants", {})
    for name in sorted(set(src_const) & set(aot_const)):
        if src_const[name] != aot_const[name]:
            diffs.append(
                name + " 的常量值不同：" + src_const[name] + " vs " + aot_const[name]
            )
    return diffs


def probe_module(source_path: Path, module_name: str, pyd: Path, init_symbol: str) -> dict:
    """对一个模块做双进程差分探测 ✅。

    返回 {"ok": bool, "diffs": [...], "error": str}
    """
    source_path = Path(source_path).resolve()
    pyd = Path(pyd).resolve()
    src = _run({"mode": "source", "path": str(source_path), "module": module_name})
    aot = _run({"mode": "aot", "path": str(pyd), "module": module_name, "init": init_symbol})
    diffs = compare(src, aot)
    return {
        "ok": not diffs,
        "diffs": diffs,
        "error": (src.get("error") or aot.get("error") or ""),
    }