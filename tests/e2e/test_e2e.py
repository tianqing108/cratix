"""端到端测试：构建 hello 示例 -> 运行 -> 零落盘 -> 可复现。

需要 MSVC 工具链 ✅（没有则跳过 ✅）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
EXAMPLE = ROOT / "examples" / "hello"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.packaging import verify


def _have_msvc() -> bool:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    return vswhere.exists()


@unittest.skipUnless(_have_msvc(), "需要 MSVC 工具链")
class EndToEndTest(unittest.TestCase):

    @classmethod
    def _build(cls):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "cratix", "build", "--config", str(EXAMPLE / "cratix.toml")],
            cwd=str(EXAMPLE),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        if proc.returncode != 0:
            raise AssertionError("构建失败：" + proc.stdout[-2000:] + proc.stderr[-2000:])
        return proc

    def setUp(self):
        self._build()
        self.exe = EXAMPLE / "build" / "public" / "hello_0.1.0.exe"
        self.assertTrue(self.exe.is_file(), "产物不存在")

    def test_01_runs_and_exits_zero(self):
        proc = subprocess.run(
            [str(self.exe)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("hello from cratix", proc.stdout)

    def test_02_zero_disk(self):
        result = verify.zero_disk_check(self.exe)
        self.assertTrue(result["zero_disk"], "运行期落盘了：" + str(result["runtime_files"]))
        self.assertTrue(result["smoke_ok"], "冒烟自检失败")

    def test_03_no_console_start(self):
        result = verify.no_console_check(self.exe)
        self.assertTrue(result["ok"], "无标准句柄启动退出码 " + str(result["exit_code"]))

    def test_04_reproducible(self):
        def digest():
            return hashlib.sha256(self.exe.read_bytes()).hexdigest()

        first = digest()
        self._build()
        self.assertEqual(first, digest(), "两次构建字节不一致")

    def test_05_onedir_runs(self):
        exe = EXAMPLE / "build" / "public" / "hello_0.1.0" / "hello.exe"
        self.assertTrue(exe.is_file(), "onedir 产物不存在")
        self.assertTrue((exe.parent / "payload.zip").is_file(), "onedir 载荷缺失")
        proc = subprocess.run(
            [str(exe)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("hello from cratix", proc.stdout)

    def test_06_onedir_zero_disk(self):
        exe = EXAMPLE / "build" / "public" / "hello_0.1.0" / "hello.exe"
        if not exe.is_file():
            self.skipTest("onedir 产物不存在")
        result = verify.zero_disk_check(exe)
        self.assertTrue(result["zero_disk"], "运行期落盘了：" + str(result["runtime_files"]))


if __name__ == "__main__":
    unittest.main()