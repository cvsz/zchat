"""Optional CV/OCR helpers for Camfrog screenshot evidence.

These functions operate only on a screenshot explicitly provided by the local
agent. They do not infer a Camfrog-specific UI layout or claim friend/presence
semantics. Install requirements-vision.txt and the Tesseract executable to use
OCR. All functions return ``semantic_state_verified=False`` — raw CV output is
never proof of a friend identity or online/offline state.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def image_sha256(path: str | Path) -> str:
    """Return the SHA-256 hex digest of an image file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ocr_image(
    path: str | Path,
    *,
    lang: str = "eng",
) -> dict[str, Any]:
    """Run OCR over an explicitly selected image.

    Returns raw text and metadata. Requires ``pytesseract`` and Pillow plus the
    Tesseract executable.

    Args:
        path: Path to the screenshot image.
        lang: Tesseract language code.

    Returns:
        Dictionary with path, sha256, text, mean_word_confidence, word_count,
        and semantic_state_verified=False.

    Raises:
        FileNotFoundError: If the image path does not exist.
        RuntimeError: If OCR dependencies are not installed.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))

    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies are optional. Install requirements-vision.txt first."
        ) from exc
    with Image.open(p) as img:
        text = pytesseract.image_to_string(img, lang=lang)
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT
        )
    confidences: list[float] = []
    for value in data.get("conf", []):
        try:
            n = float(value)
            if n >= 0:
                confidences.append(n)
        except (TypeError, ValueError):
            continue
    mean_conf = (
        round(sum(confidences) / len(confidences), 2) if confidences else None
    )
    word_count = len([x for x in data.get("text", []) if str(x).strip()])
    return {
        "path": str(p.resolve()),
        "sha256": image_sha256(p),
        "text": text,
        "mean_word_confidence": mean_conf,
        "word_count": word_count,
        "semantic_state_verified": False,
        "note": (
            "OCR output is raw text; it is not proof of a friend identity or "
            "online/offline state."
        ),
    }


def template_match(
    screenshot_path: str | Path,
    template_path: str | Path,
    *,
    threshold: float = 0.90,
) -> dict[str, Any]:
    """Optional OpenCV template match; requires a target-specific template image.

    Args:
        screenshot_path: Path to the screenshot image.
        template_path: Path to the template image.
        threshold: Match score threshold between 0 and 1.

    Returns:
        Dictionary with match, score, threshold, rectangle, and
        semantic_state_verified=False.

    Raises:
        ValueError: If threshold is out of range or images cannot be read.
        RuntimeError: If OpenCV is not installed.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is optional. Install requirements-vision.txt first."
        ) from exc
    image = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if image is None or template is None:
        raise ValueError("Could not read screenshot or template image")
    ih, iw = image.shape[:2]
    th, tw = template.shape[:2]
    if th > ih or tw > iw:
        raise ValueError("Template cannot be larger than screenshot")
    result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, max_score, _, max_location = cv2.minMaxLoc(result)
    x, y = max_location
    return {
        "match": bool(max_score >= threshold),
        "score": float(max_score),
        "threshold": threshold,
        "rectangle": [int(x), int(y), int(x + tw), int(y + th)],
        "semantic_state_verified": False,
    }
