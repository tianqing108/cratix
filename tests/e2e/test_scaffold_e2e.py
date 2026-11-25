"""脚手架闭环的端到端测试 ✅。

**init → build → run → verify** ✅ —— 验证新用户的最短路径 ✅ ✅。
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _have_msvc() -> bool:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    return vswhere.exists()


class ScaffoldEndToEndTest(unittest.TestCase):
    def test_01_init_creates_a_buildable_project(self):
        from cratix.scaffold import scaffold

        target = ROOT / "build" / "scaffold_demo"
        if target.exists():
            import shutil

            shutil.rmtree(target, ignore_errors=True)
        files = scaffold(target, "demo", "demoapp")
        self.assertTrue((target / "cratix.toml").is_file())
        self.assertTrue((target / "demoapp" / "app.py").is_file())
        self.assertEqual(len(files), 4)

    @unittest.skipUnless(_have_msvc(), "需要 MSVC 工具链")
    def test_02_scaffolded_project_builds_and_runs(self):
        from cratix.scaffold import scaffold

        target = ROOT / "build" / "scaffold_demo"
        if not (target / "cratix.toml").is_file():
            scaffold(target, "demo", "demoapp")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "cratix", "build", "--config", str(target / "cratix.toml")],
            cwd=str(target),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        exe = target / "build" / "public" / "demo_0.1.0.exe"
        self.assertTrue(exe.is_file(), "产物不存在")
        run = subprocess.run(
            [str(exe)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("hello, world", run.stdout)


if __name__ == "__main__":
    unittest.main()