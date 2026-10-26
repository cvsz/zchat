"""Tests for camfrog.friend_presence_parser — conservative friend presence."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "camfrog"))

from friend_presence_parser import make_observation, normalize_presence, now_iso


def test_unknown_presence_is_not_inferred_as_offline():
    assert (
        normalize_presence("missing", online_labels={"Online"}, offline_labels={"Offline"})
        == "unknown"
    )


def test_empty_label_is_unknown():
    assert normalize_presence("", online_labels={"Online"}, offline_labels={"Offline"}) == "unknown"


def test_online_label_maps_to_online():
    assert (
        normalize_presence("Online", online_labels={"Online"}, offline_labels={"Offline"})
        == "online"
    )


def test_offline_label_maps_to_offline():
    assert (
        normalize_presence("Offline", online_labels={"Online"}, offline_labels={"Offline"})
        == "offline"
    )


def test_whitespace_normalized():
    assert (
        normalize_presence("  Online  ", online_labels={"Online"}, offline_labels={"Offline"})
        == "online"
    )


def test_case_insensitive():
    assert (
        normalize_presence("oNlInE", online_labels={"Online"}, offline_labels={"Offline"})
        == "online"
    )


def test_ambiguous_label_is_unknown():
    """A label in both online and offline sets returns unknown (fail closed)."""
    assert (
        normalize_presence("Idle", online_labels={"Idle"}, offline_labels={"Idle"})
        == "unknown"
    )


def test_multiword_labels():
    online = {"Away From Keyboard", "Online"}
    offline = {"Offline"}
    assert (
        normalize_presence("away from keyboard", online_labels=online, offline_labels=offline)
        == "online"
    )


def test_make_observation_normalizes_name():
    result = make_observation(
        "  Example Friend  ",
        "Online",
        online_labels={"Online"},
        offline_labels={"Offline"},
        source="uia",
        observed_at="2026-10-26T00:00:00Z",
        confidence=98.0,
    )
    assert result["display_name"] == "Example Friend"
    assert result["presence"] == "online"
    assert result["source"] == "uia"
    assert result["confidence"] == 98.0
    assert result["verified"] is False


def test_make_observation_defaults_observed_at():
    before = now_iso()
    result = make_observation(
        "Alice",
        "Offline",
        online_labels={"Online"},
        offline_labels={"Offline"},
        source="ocr",
    )
    after = now_iso()
    # observed_at should fall between before/after (compare up to seconds)
    assert before[:19] <= result["observed_at"][:19] <= after[:19]
    assert result["presence"] == "offline"


def test_make_observation_rejects_empty_name():
    try:
        make_observation(
            "   ",
            "Online",
            online_labels={"Online"},
            offline_labels={"Offline"},
            source="uia",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Empty display_name should raise ValueError")


def test_make_observation_rejects_out_of_range_confidence():
    try:
        make_observation(
            "Alice",
            "Online",
            online_labels={"Online"},
            offline_labels={"Offline"},
            source="uia",
            confidence=150.0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Confidence > 100 should raise ValueError")


def test_now_iso_returns_iso_format():
    value = now_iso()
    assert value.endswith("Z") or "+" in value
    assert "T" in value
