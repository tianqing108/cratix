"""严格 AOT 模式 ✅（CI 用 ✅）。

验收线 ✅：AOT 覆盖率不是 100% 就让**构建失败** ✅ —— 这样"静默回退"在 CI 里藏不住 ✅。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.build.pipeline import build
from cratix.config import load_config

TOML = """[project]
name = "strictdemo"
version = "0.1.0"
entry = "demo.main:main"

[build]
targets = ["onefile"]
console = true

[compiler]
backend = "aot"
probe = "off"
strict_aot = {strict}
"""

CLEAN_MAIN = '''"""干净模块：全部可 AOT ✅。"""


def helper(value):
    return value * 2


def main():
    print("strict demo", helper(21))
'''

CLOSURE_MAIN = '''"""含闭包：必然回退 ✅。"""


def make_adder(step):
    def adder(value):
        return value + step

    return adder


def main():
    print("strict demo", make_adder(1)(2))
'''


def _make_project(root: Path, source: str, strict: bool) -> Path:
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (root / "demo" / "main.py").write_text(source, encoding="utf-8")
    (root / "cratix.toml").write_text(
        TOML.format(strict="true" if strict else "false"), encoding="utf-8"
    )
    return root / "cratix.toml"


def setUpModule():
    """**管道/GBK 控制台下 payload 日志会 UnicodeEncodeError** ✅ ——
    CLI 已经会把 stdout/stderr 重配成 utf-8 ✅，测试里也照做 ✅（否则是环境假失败 ✅）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:  # noqa: BLE001 - 非 tty 时没有 reconfigure ✅
            pass


def _workdir():
    """**不用 TemporaryDirectory** ✅ —— Windows 上并行压缩的子进程可能还握着目录句柄 ✅，
    清理会抛 OSError ✅（测试就假失败了 ✅）。这里显式 rmtree 且忽略错误 ✅。"""
    return Path(tempfile.mkdtemp(prefix="cratix-strict-"))


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@unittest.skipUnless(sys.platform == "win32", "构建链路目前只有 Windows 实现 ✅")
class StrictAotTest(unittest.TestCase):
    def test_clean_project_passes_and_reports_full_coverage(self):
        tmp = _workdir()
        try:
            config = _make_project(tmp, CLEAN_MAIN, strict=True)
            cfg = load_config(config)
            self.assertTrue(cfg.strict_aot)
            outcome = build(cfg, log=lambda *_a, **_k: None)
            self.assertTrue(outcome.ok, outcome.error)
            coverage = outcome.report["aot"]["coverage"]
            self.assertEqual(coverage["functions_fallback"], 0)
            self.assertGreaterEqual(coverage["functions_aot"], 2)
            self.assertEqual(coverage["ratio"], 1.0)
            self.assertTrue(outcome.report["strict_aot"])
        finally:
            _cleanup(tmp)

    def test_fallback_fails_the_build(self):
        tmp = _workdir()
        try:
            config = _make_project(tmp, CLOSURE_MAIN, strict=True)
            cfg = load_config(config)
            outcome = build(cfg, log=lambda *_a, **_k: None)
            self.assertFalse(outcome.ok, "有函数回退时严格模式必须失败")
            self.assertIn("strict-aot", outcome.error)
            self.assertGreater(outcome.report["aot"]["coverage"]["functions_fallback"], 0)
        finally:
            _cleanup(tmp)

    def test_non_strict_project_still_succeeds_with_fallback(self):
        tmp = _workdir()
        try:
            config = _make_project(tmp, CLOSURE_MAIN, strict=False)
            cfg = load_config(config)
            outcome = build(cfg, log=lambda *_a, **_k: None)
            self.assertTrue(outcome.ok, outcome.error)
            self.assertGreater(outcome.report["aot"]["coverage"]["functions_fallback"], 0)
        finally:
            _cleanup(tmp)


if __name__ == "__main__":
    unittest.main()
