"""打包半边（vendor 来的零件）的单元测试。"""

from __future__ import annotations

import struct
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from cratix.packaging import assemble, defaults, pe, sysdll


class SysDllTest(unittest.TestCase):
    def test_api_sets_are_system(self):
        self.assertTrue(sysdll.is_system_only("api-ms-win-crt-runtime-l1-1-0.dll"))

    def test_known_system_dll(self):
        self.assertTrue(sysdll.is_system_only("KERNEL32.dll"))

    def test_unknown_dll_is_not_system(self):
        self.assertFalse(sysdll.is_system_only("definitely-not-a-system-dll-xyz.dll"))

    def test_stable_abi_alias(self):
        self.assertEqual(sysdll.resolve_alias("python3.dll"), "python314.dll")


class DefaultsTest(unittest.TestCase):
    def test_policy_is_configurable(self):
        snapshot = defaults.apply_policy(
            {
                "exclude_modules": ["a.b"],
                "exclude_packages": ["evil"],
                "project_tops": ["mine"],
            }
        )
        self.assertIn("a.b", snapshot["exclude_modules"])
        self.assertIn("evil", snapshot["exclude_packages"])
        self.assertIn("mine", snapshot["project_tops"])
        self.assertTrue(defaults.is_excluded_module("a.b.c"))
        self.assertTrue(defaults.is_excluded_package("evil"))


class PeTest(unittest.TestCase):
    def test_parse_garbage_degrades(self):
        info = pe.parse_bytes(b"NOTPE" * 64, Path("<memory>"))
        self.assertIn("<解析失败>", info.imports)


class AssembleTest(unittest.TestCase):
    def _payload(self, path: Path, with_meta: bool = True) -> Path:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("entry.txt", "pkg:main")
            if with_meta:
                zf.writestr("meta.json", "{}")
        return path

    def test_rejects_non_pe_stub(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "stub.exe"
            stub.write_bytes(b"NOTPE")
            payload = self._payload(root / "payload.zip")
            with self.assertRaises(SystemExit):
                assemble.assemble(stub, payload, root / "out.exe")

    def test_rejects_non_zip_payload(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "stub.exe"
            stub.write_bytes(b"MZ" + b"\x00" * 64)
            payload = root / "payload.zip"
            payload.write_bytes(b"PK-not-really")
            with self.assertRaises(SystemExit):
                assemble.assemble(stub, payload, root / "out.exe")

    def test_requires_control_entries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "stub.exe"
            stub.write_bytes(b"MZ" + b"\x00" * 64)
            payload = self._payload(root / "payload.zip", with_meta=False)
            with self.assertRaises(SystemExit):
                assemble.assemble(stub, payload, root / "out.exe")

    def test_produces_cookie(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "stub.exe"
            stub.write_bytes(b"MZ" + b"\x00" * 64)
            payload = self._payload(root / "payload.zip")
            out = root / "out.exe"
            assemble.assemble(stub, payload, out)
            data = out.read_bytes()
            self.assertTrue(data.startswith(b"MZ"))
            cookie = data[-32:]
            self.assertTrue(cookie.startswith(assemble.COOKIE_MAGIC))
            off, size = struct.unpack_from("<QQ", cookie, 8)
            self.assertEqual(off, 66)  # stub 字节数：b"MZ" + 64 个 0x00
            self.assertEqual(size, len(payload.read_bytes()))


if __name__ == "__main__":
    unittest.main()