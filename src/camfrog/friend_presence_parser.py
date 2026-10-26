"""Conservative parser for manually verified UIA/OCR friend observations.

Do not configure presence labels until they have been observed on the exact
Camfrog Client version. Unknown labels remain unknown; the parser does not
infer Offline from absence in a screenshot. Friend observations are never
claimed as verified — verification requires an independent reviewer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

Presence = Literal["online", "offline", "unknown"]


@dataclass(frozen=True)
class FriendObservation:
    """A single friend presence observation from UIA or OCR."""

    display_name: str
    presence: Presence
    source: str
    confidence: float | None
    observed_at: str
    verified: bool = False


def now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def normalize_presence(
    raw_label: str,
    *,
    online_labels: set[str],
    offline_labels: set[str],
) -> Presence:
    """Normalize a raw presence label into online/offline/unknown.

    Unknown labels (including empty strings) return ``"unknown"``. A label
    present in both online and offline sets returns ``"unknown"`` to fail
    closed on ambiguity.
    """
    label = " ".join(raw_label.casefold().split())
    if not label:
        return "unknown"
    online = {" ".join(x.casefold().split()) for x in online_labels}
    offline = {" ".join(x.casefold().split()) for x in offline_labels}
    in_online = label in online
    in_offline = label in offline
    if in_online and not in_offline:
        return "online"
    if in_offline and not in_online:
        return "offline"
    return "unknown"


def make_observation(
    display_name: str,
    raw_presence_label: str,
    *,
    online_labels: set[str],
    offline_labels: set[str],
    source: str,
    observed_at: str | None = None,
    confidence: float | None = None,
    verified: bool = False,
) -> dict:
    """Create a friend presence observation dictionary.

    Args:
        display_name: The friend's display name (whitespace-normalized).
        raw_presence_label: The raw presence label observed from UIA/OCR.
        online_labels: Labels that mean "online" on this client version.
        offline_labels: Labels that mean "offline" on this client version.
        source: Observation source, e.g. "uia" or "ocr".
        observed_at: ISO 8601 timestamp; defaults to now.
        confidence: Optional confidence score between 0 and 100.
        verified: Whether an independent reviewer has verified this observation.

    Returns:
        A dictionary suitable for JSON serialization.

    Raises:
        ValueError: If display_name is empty or confidence is out of range.
    """
    name = " ".join(display_name.split())
    if not name:
        raise ValueError("display_name must not be empty")
    if confidence is not None and not 0.0 <= confidence <= 100.0:
        raise ValueError("confidence must be between 0 and 100")
    result = FriendObservation(
        display_name=name,
        presence=normalize_presence(
            raw_presence_label,
            online_labels=online_labels,
            offline_labels=offline_labels,
        ),
        source=source,
        confidence=confidence,
        observed_at=observed_at or now_iso(),
        verified=verified,
    )
    return asdict(result)
