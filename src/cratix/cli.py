"""cratix 命令行入口。

退出码约定：
    0  成功
    1  构建失败（依赖 / 编译 / 探针 / 载荷校验）
    2  产物运行期校验失败
    3  用法错误（参数 / 配置）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .build.pipeline import build
from .config import ConfigError, find_config, load_config

EXIT_OK = 0
EXIT_BUILD_FAIL = 1
EXIT_RUNTIME_FAIL = 2
EXIT_USAGE = 3

VERIFY_TIMEOUT_SEC = 120


def _reconfigure_streams() -> None:
    """控制台默认 GBK —— 打印含替换字符的文本会 UnicodeEncodeError 直接打断构建 ✅。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:  # noqa: BLE001 - 非 tty 时没有 reconfigure ✅
            pass


def _cmd_build(args) -> int:
    config_path = Path(args.config).resolve() if args.config else find_config()
    overrides = {
        "target": args.target or None,
        "entry": args.entry or None,
        "console": True if args.console else (False if args.no_console else None),
        "icon": args.icon or None,
        "backend": args.backend,
        "probe": args.probe,
    }
    cfg = load_config(config_path, overrides)
    print(f"[cratix] 项目：{cfg.name} {cfg.version}  入口：{cfg.entry}")
    print("[cratix] 目标：" + ", ".join(cfg.targets) + "  后端：" + cfg.backend)
    outcome = build(cfg)
    return EXIT_OK if outcome.ok else EXIT_BUILD_FAIL


def _cmd_deps(args) -> int:
    from .packaging import deps

    config_path = Path(args.config).resolve() if args.config else find_config()
    cfg = load_config(config_path, {"entry": args.entry or None})
    rel = cfg.entry_module.replace(".", "/")
    entry_file = None
    for candidate in (cfg.project_root / (rel + ".py"), cfg.project_root / rel / "__init__.py"):
        if candidate.is_file():
            entry_file = candidate
            break
    if entry_file is None:
        print(f"[cratix] 找不到入口模块源码：{cfg.entry_module}", file=sys.stderr)
        return EXIT_USAGE
    closure = deps.collect(cfg.project_root, entry_file)
    print(f"模块 {len(closure.modules)} 个 / 文件 {len(closure.files)} 个")
    if closure.unresolved:
        print("未解析的本地导入：" + ", ".join(sorted(closure.unresolved)[:20]))
    for name in sorted(closure.modules):
        print("  " + name)
    return EXIT_OK


def _pick_exe(cfg, explicit: str):
    if explicit:
        path = Path(explicit).resolve()
        return path if path.is_file() else None
    out = cfg.out_dir
    if not out.is_dir():
        return None
    candidates = sorted(out.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _cmd_verify(args) -> int:
    from .packaging import verify

    config_path = Path(args.config).resolve() if args.config else find_config()
    cfg = load_config(config_path)
    exe = _pick_exe(cfg, args.exe)
    if exe is None:
        print("[cratix] 找不到产物（先跑 cratix build）", file=sys.stderr)
        return EXIT_RUNTIME_FAIL
    print(f"[cratix] 验证：{exe}")
    ok = True
    if args.zero_disk:
        result = verify.zero_disk_check(exe)
        print("[cratix] 零落盘复检：" + ("PASS" if result["zero_disk"] else "FAIL"))
        if result["runtime_files"]:
            print("  运行期新增文件：" + ", ".join(result["runtime_files"][:10]))
        if result["exit_code"] != 0:
            print("  退出码 " + str(result["exit_code"]))
        ok = ok and result["ok"]
    if args.no_console:
        result = verify.no_console_check(exe)
        print(
            "[cratix] 无标准句柄启动："
            + ("PASS" if result["ok"] else "FAIL")
            + "（退出码 " + str(result["exit_code"]) + "）"
        )
        ok = ok and result["ok"]
    if not args.zero_disk and not args.no_console:
        env = verify.sanitized_env({"CRATIX_ENTRY": "cratix_smoke:run"})
        proc = subprocess.Popen(
            [str(exe)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            out, err = proc.communicate(timeout=VERIFY_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"[cratix] 产物超时（>{VERIFY_TIMEOUT_SEC}s）", file=sys.stderr)
            return EXIT_RUNTIME_FAIL
        stdout = out.decode("utf-8", "replace").strip()
        stderr = err.decode("utf-8", "replace").strip()
        if stdout:
            print("  stdout: " + stdout)
        if stderr:
            print("  stderr: " + stderr)
        ok = proc.returncode == 0
        print("[cratix] 产物运行：" + ("PASS" if ok else "FAIL") + "（退出码 " + str(proc.returncode) + "）")
    return EXIT_OK if ok else EXIT_RUNTIME_FAIL


def _cmd_init(args) -> int:
    """脚手架 ✅ —— 生成一个最小可用的 cratix 项目 ✅。"""
    from .scaffold import scaffold

    target = Path(args.directory).resolve()
    created = scaffold(target, args.name, args.package)
    print("[cratix] 已生成：" + str(target))
    for path in created:
        print("  " + str(path.relative_to(target)))
    print("下一步：cd " + str(target) + "  然后 python -m cratix build")
    return EXIT_OK


def _cmd_info(args) -> int:
    """显示**生效的**配置 ✅（合并后的 ✅ + 裁剪表 ✅）。"""
    from .packaging import defaults

    config_path = Path(args.config).resolve() if args.config else find_config()
    if config_path is None:
        print("[cratix] 附近没有 cratix.toml（用 --config 指定 ✅）")
        return EXIT_USAGE
    cfg = load_config(config_path)
    print("配置文件：" + str(cfg.path))
    print("项目根  ：" + str(cfg.project_root))
    print("产品    ：" + cfg.name + " " + cfg.version)
    print("入口    ：" + cfg.entry)
    print("目标    ：" + ", ".join(cfg.targets))
    print("后端    ：" + cfg.backend + "（探针 " + cfg.probe + "）")
    print("控制台  ：" + ("保留" if cfg.console else "无"))
    policy = defaults.apply_policy(cfg.policy)
    print("裁剪模块：" + (", ".join(policy["exclude_modules"]) or "（无）"))
    print("裁剪包  ：" + (", ".join(policy["exclude_packages"]) or "（无）"))
    print("进包原生：" + ", ".join(policy["native_in_package"]))
    print("项目顶层：" + (", ".join(policy["project_tops"]) or "（未声明 ✅）"))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    parser = argparse.ArgumentParser(prog="cratix", description="自研 Python 打包工具")
    parser.add_argument("--version", action="version", version="cratix " + __version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="构建产物")
    p_build.add_argument("--config", default="", help="cratix.toml 路径（默认向上查找）")
    p_build.add_argument("--target", action="append", choices=["onefile", "onedir"], help="构建目标（可重复）")
    p_build.add_argument("--entry", default="", help="入口 module:attr")
    p_build.add_argument("--console", action="store_true", help="保留控制台窗口（默认 GUI 子系统）")
    p_build.add_argument("--no-console", action="store_true", help="强制无控制台")
    p_build.add_argument("--icon", default="", help="exe 图标（.ico）")
    p_build.add_argument("--backend", choices=["pyc", "aot"], help="编译后端")
    p_build.add_argument("--probe", choices=["on", "off", "strict"], help="差分探针模式")
    p_build.set_defaults(func=_cmd_build)

    p_deps = sub.add_parser("deps", help="打印依赖闭包")
    p_deps.add_argument("--config", default="")
    p_deps.add_argument("--entry", default="")
    p_deps.set_defaults(func=_cmd_deps)

    p_verify = sub.add_parser("verify", help="运行产物做冒烟验证")
    p_verify.add_argument("--config", default="")
    p_verify.add_argument("--exe", default="", help="产物路径（默认取 build/public 最新）")
    p_verify.add_argument("--zero-disk", action="store_true", help="零落盘复检（独立 TEMP 目录 before/after）")
    p_verify.add_argument("--no-console", action="store_true", help="无标准句柄启动复检（= Explorer 双击）")
    p_verify.set_defaults(func=_cmd_verify)

    p_init = sub.add_parser("init", help="生成项目脚手架")
    p_init.add_argument("directory", help="目标目录")
    p_init.add_argument("--name", default="", help="产品名（默认取目录名）")
    p_init.add_argument("--package", default="", help="包名（默认 app）")
    p_init.set_defaults(func=_cmd_init)

    p_info = sub.add_parser("info", help="显示生效的配置")
    p_info.add_argument("--config", default="")
    p_info.set_defaults(func=_cmd_info)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"[cratix] 配置错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            return EXIT_BUILD_FAIL
        return int(code) if code else EXIT_OK
    except Exception:  # noqa: BLE001 - 兜底：未预期异常也要给明确的退出码 ✅
        import traceback

        print("[cratix] 未预期的错误：", file=sys.stderr)
        traceback.print_exc()
        return EXIT_BUILD_FAIL