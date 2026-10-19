import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

LINE_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = LINE_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from write_build_manifest import write_build_manifest


class BuildManifestTests(unittest.TestCase):
    def test_writes_sha256_for_staged_line_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "line-status-changer.exe"
            executable.write_bytes(b"packaged executable bytes")

            manifest = write_build_manifest(root)

            expected = hashlib.sha256(executable.read_bytes()).hexdigest()
            self.assertEqual(
                manifest.read_text(encoding="ascii"),
                f"{expected}  line-status-changer.exe\n",
            )

    def test_rejects_missing_staged_executable_without_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with self.assertRaises(FileNotFoundError):
                write_build_manifest(root)

            self.assertFalse((root / "SHA256SUMS.txt").exists())


if __name__ == "__main__":
    unittest.main()
