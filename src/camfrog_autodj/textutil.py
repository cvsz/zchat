"""Text utilities."""
from __future__ import annotations
import re

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]")
_MULTI_SPACE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Remove zero-width chars, collapse whitespace, strip."""
    if not text:
        return ""
    text = _ZERO_WIDTH.sub("", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def norm(text: str) -> str:
    """Normalize for echo detection: strip, collapse spaces, lowercase."""
    return _MULTI_SPACE.sub(" ", text.strip().lower())