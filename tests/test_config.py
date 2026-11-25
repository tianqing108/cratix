"""配置读取与校验的单元测试。"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cratix.config import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def _write(self, root: Path, body: str) -> Path:
        path = root / "cratix.toml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_minimal_config(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            path = self._write(
                root,
                """
                [project]
                name = "demo"
                version = "1.2.3"
                entry = "pkg.app:main"
                """
            )
            cfg = load_config(path)
            self.assertEqual(cfg.name, "demo")
            self.assertEqual(cfg.version, "1.2.3")
            self.assertEqual(cfg.entry_module, "pkg.app")
            self.assertEqual(cfg.entry_attr, "main")
            self.assertEqual(cfg.targets, ["onefile"])

    def test_entry_must_be_module_attr(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(root, """[project]\nentry = "nocolon"\n""")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_entry_attr_must_be_identifier(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(root, """[project]\nentry = "pkg:a.b"\n""")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_unknown_target_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(
                root,
                """
                [project]
                entry = "pkg:main"
                [build]
                targets = ["weird"]
                """
            )
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_cli_override(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(root, """[project]\nentry = "pkg:main"\n""")
            cfg = load_config(path, {"target": ["onedir"], "entry": "pkg:other"})
            self.assertEqual(cfg.targets, ["onedir"])
            self.assertEqual(cfg.entry_attr, "other")


if __name__ == "__main__":
    unittest.main()