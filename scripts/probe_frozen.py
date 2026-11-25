"""**冻结 exe 体检** ✅ —— 构建 examples/notes，再用不同数据跑它的子命令 ✅。

为什么单独写这个 ✅：进程内加载编译产物**一切正常** ✅，但**打包后的 exe**
在读/写**磁盘文件**时出问题（#131 ✅）——这类问题只有跑真 exe 才看得见 ✅。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> 仓库根 ✅
EXAMPLE = ROOT / "examples" / "notes"
EXE = EXAMPLE / "build" / "public" / "notes_0.2.0.exe"
WORK = ROOT / "build" / "frozen_work"


def run(args, cwd=WORK, timeout=60):
    env = dict(os.environ)
    env["CRATIX_NO_ERRORBOX"] = "1"
    proc = subprocess.run(
        [str(EXE), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        timeout=timeout,
    )
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode, (tail[-1] if tail else ""), (proc.stderr or "")[-200:]


def main() -> int:
    if not EXE.is_file():
        print("先构建：cd examples/notes && python -m cratix build --config cratix.toml")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    cases = []

    # 1) **没有数据文件** ✅（最基础）
    for name in ("a.json",):
        target = WORK / name
        if target.exists():
            target.unlink()
    cases.append(("无数据文件 stats", ["--store", "a.json", "stats"]))

    # 2) **空文件** ✅
    (WORK / "b.json").write_text("", encoding="utf-8")
    cases.append(("空文件 stats", ["--store", "b.json", "stats"]))

    # 3) **空数组** ✅
    (WORK / "c.json").write_text("[]", encoding="utf-8")
    cases.append(("空数组 stats", ["--store", "c.json", "stats"]))

    # 4) **一条数据** ✅ <- 目前这里就出问题 ✅
    (WORK / "d.json").write_text(
        json.dumps([{"text": "x", "category": "work", "done": False}]),
        encoding="utf-8",
    )
    cases.append(("一条数据 stats", ["--store", "d.json", "stats"]))
    cases.append(("一条数据 list", ["--store", "d.json", "list"]))

    # 5) **写入** ✅
    (WORK / "e.json").unlink(missing_ok=True)
    cases.append(("add 写文件", ["--store", "e.json", "add", "hello"]))

    failed = 0
    for title, args in cases:
        try:
            code, last, err = run(args)
        except Exception as exc:  # noqa: BLE001
            print("%-16s 异常：%s" % (title, exc))
            failed += 1
            continue
        ok = code == 0
        print("%-16s rc=%-4d %s %s" % (title, code, "OK  " if ok else "FAIL", last[:60]))
        if not ok:
            failed += 1
            if err.strip():
                print("                stderr: " + err.strip()[:120])
    print()
    print("失败 %d / %d" % (failed, len(cases)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
