"""Unicode-aware comparison helpers."""

from __future__ import annotations

import unicodedata


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFC", " ".join(value.split()))


def texts_equal(a: str | None, b: str | None) -> bool:
    return normalize_text(a) == normalize_text(b)
