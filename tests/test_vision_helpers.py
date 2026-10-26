"""Tests for camfrog.vision_helpers — optional CV/OCR helpers."""
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "camfrog"))

import pytest
from vision_helpers import image_sha256, ocr_image, template_match

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Vision/OCR helpers require Windows Tesseract/OpenCV; behavior tested on import paths only.",
)


def test_image_sha256(tmp_path):
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    digest = image_sha256(img)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_image_sha256_deterministic(tmp_path):
    img = tmp_path / "det.png"
    data = b"deterministic-content"
    img.write_bytes(data)
    assert image_sha256(img) == image_sha256(img)


def test_ocr_image_missing_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.png"
    with pytest.raises(FileNotFoundError):
        ocr_image(missing)


def test_template_match_threshold_validation(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    b.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    with pytest.raises(ValueError, match="threshold must be between 0 and 1"):
        template_match(a, b, threshold=1.5)
    with pytest.raises(ValueError, match="threshold must be between 0 and 1"):
        template_match(a, b, threshold=-0.1)


def test_template_match_images_too_small(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    b.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    with pytest.raises(ValueError, match="Template cannot be larger than screenshot"):
        template_match(a, b, threshold=0.9)


def _block_import(name_to_block):
    """Return a fake __import__ that blocks the named module."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == name_to_block or name.startswith(name_to_block + "."):
            raise ImportError(f"No module named {name}")
        return real_import(name, *args, **kwargs)

    return fake_import


def test_ocr_missing_dependency(monkeypatch):
    """OCR should raise RuntimeError when pytesseract is unavailable."""
    monkeypatch.setattr(builtins, "__import__", _block_import("pytesseract"))
    tmp = Path("x.png")
    with pytest.raises(RuntimeError, match="OCR dependencies are optional"):
        ocr_image(tmp)


def test_template_match_missing_dependency(monkeypatch):
    """template_match should raise RuntimeError when cv2 is unavailable."""
    monkeypatch.setattr(builtins, "__import__", _block_import("cv2"))
    tmp = Path("x.png")
    with pytest.raises(RuntimeError, match="OpenCV is optional"):
        template_match(tmp, tmp, threshold=0.9)
