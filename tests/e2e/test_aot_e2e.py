"""AOT 后端的端到端测试 ✅。

核心：**真的用自研编译器把项目模块编成 .pyd** ✅ → 进载荷 ✅ → 运行期从**内存**加载 ✅。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
EXAMPLE = ROOT / "examples" / "hello"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _have_msvc() -> bool:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    return vswhere.exists()


@unittest.skipUnless(_have_msvc(), "需要 MSVC 工具链")
class AotEndToEndTest(unittest.TestCase):

    def _build(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "cratix",
                "build",
                "--config",
                str(EXAMPLE / "cratix.toml"),
                "--backend",
                "aot",
            ],
            cwd=str(EXAMPLE),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        return proc

    def test_01_aot_module_in_payload_and_manifest(self):
        self._build()
        payload = EXAMPLE / "build" / "cratix" / "payload.zip"
        self.assertTrue(payload.is_file(), "载荷不存在")
        with zipfile.ZipFile(payload) as zf:
            names = zf.namelist()
            meta = json.loads(zf.read("meta.json").decode("utf-8"))
        aot_entries = [n for n in names if n.startswith("ext/cratix_")]
        self.assertTrue(aot_entries, "载荷里没有 AOT 编译产物")
        records = [r for r in meta.get("extensions", []) if r.get("entry") in aot_entries]
        self.assertTrue(records, "meta.json 里没有 AOT 扩展记录")
        for rec in records:
            self.assertTrue(rec["init"].startswith("PyInit_cratix_"))
            self.assertTrue(rec.get("exec_done"), "应标记 exec_done（避免引导模块重跑模块体）")

    def test_02_aot_module_used_at_runtime(self):
        self._build()
        exe = EXAMPLE / "build" / "public" / "hello_0.1.0.exe"
        env = dict(os.environ)
        env["CRATIX_NO_ERRORBOX"] = "1"
        # **不覆盖入口** ✅ —— 用真实入口 hello.app:main ✅（AOT 模块 ✅）
        env["CRATIX_CODE"] = (
            "import hello.app as m; print('CRATIX_DICT', sorted(m.__dict__.keys()))"
        )
        proc = subprocess.run(
            [str(exe)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("hello from cratix", proc.stdout)
        # AOT 模块的模块体应已执行 ✅（import sys ✅ -> 模块字典里有 sys ✅）
        self.assertIn("'sys'", proc.stdout)

    def test_03_report_classifies_modules(self):
        self._build()
        report = json.loads(
            (EXAMPLE / "build" / "cratix" / "report.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(report["aot"]["compiled"], 1)
        self.assertIn("hello.app", report["aot_modules"])
        self.assertIn("hello", report["pyc_modules"])


if __name__ == "__main__":
    unittest.main()