"""Offline browser-observer contract tests; no Camfrog account required."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "web_profile_observer", ROOT / "tools" / "web_profile_observer.py"
)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class FakeNode:
    def __init__(self, text, visible=True):
        self.text = text
        self.visible = visible

    def is_visible(self):
        return self.visible

    def inner_text(self, timeout):
        assert timeout <= 5000
        return self.text


class FakeLocator:
    def __init__(self, nodes):
        self.nodes = nodes

    def count(self):
        return len(self.nodes)

    @property
    def first(self):
        return self.nodes[0]


class FakePage:
    def __init__(self, url, nodes):
        self.url = url
        self.nodes = nodes

    def locator(self, selector):
        assert selector == "[data-status]"
        return FakeLocator(self.nodes)


def test_profile_url_encoding_and_boundaries():
    assert observer.profile_url("zdevz") == "https://profiles.camfrog.com/th/zdevz"
    assert observer.profile_url("ชื่อไทย").startswith("https://profiles.camfrog.com/th/%")
    for bad in ("", "foo/bar", "user?x=y", "a\nb", "x" * 129):
        with pytest.raises(ValueError):
            observer.profile_url(bad)


@pytest.mark.parametrize(
    "url",
    [
        "https://profiles.camfrog.com.evil.test/th/zdevz",
        "http://profiles.camfrog.com/th/zdevz",
        "https://evil.test/th/zdevz",
        "https://profiles.camfrog.com/th/another",
        "https://attacker@profiles.camfrog.com/th/zdevz",
    ],
)
def test_observer_rejects_unexpected_location(url):
    result = observer.observe(
        FakePage(url, [FakeNode("HELLO")]), "zdevz", "HELLO", "[data-status]"
    )
    assert result["state"] == "UNVERIFIED"


def test_exact_visible_match_is_only_web_evidence():
    result = observer.observe(
        FakePage(observer.profile_url("zdevz"), [FakeNode("RUN-001")]),
        "zdevz",
        "RUN-001",
        "[data-status]",
    )
    assert result["state"] == "WEB_PROFILE_MATCH"
    assert result["observed_text_sha256"] == result["expected_text_sha256"]


def test_mismatch_does_not_report_success():
    result = observer.observe(
        FakePage(observer.profile_url("zdevz"), [FakeNode("old status")]),
        "zdevz",
        "RUN-001",
        "[data-status]",
    )
    assert result["state"] == "WEB_PROFILE_MISMATCH"


@pytest.mark.parametrize(
    "nodes,reason",
    [
        ([], "selector_not_unique"),
        ([FakeNode("a"), FakeNode("b")], "selector_not_unique"),
        ([FakeNode("RUN-001", visible=False)], "status_element_not_visible"),
        ([FakeNode("  ")], "empty_status_element"),
    ],
)
def test_non_verifiable_cases_fail_closed(nodes, reason):
    result = observer.observe(
        FakePage(observer.profile_url("zdevz"), nodes),
        "zdevz",
        "RUN-001",
        "[data-status]",
    )
    assert (
        result == {"state": "UNVERIFIED", "reason": reason}
        or result["reason"] == reason
    )


def test_invalid_inputs():
    page = FakePage(observer.profile_url("zdevz"), [FakeNode("RUN-001")])
    for expected, selector in [
        ("", "[data-status]"),
        ("X" * 257, "[data-status]"),
        ("good\nbad", "[data-status]"),
        ("good", "X" * 257),
    ]:
        with pytest.raises(ValueError):
            observer.observe(page, "zdevz", expected, selector)


def test_default_target_and_refresh_options(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args):
        seen["nickname"] = args.nickname
        seen["refresh_seconds"] = args.refresh_seconds
        seen["samples"] = args.samples
        return 0

    monkeypatch.setattr(observer, "run", fake_run)
    assert (
        observer.main(
            [
                "--expected",
                "RUN-001",
                "--selector",
                "[data-status]",
                "--browser-profile",
                str(tmp_path / "account-b"),
            ]
        )
        == 0
    )
    assert seen == {"nickname": "Seaza", "refresh_seconds": 30, "samples": 10}


@pytest.mark.parametrize(
    "interval,samples", [(0, 3), (9, 3), (301, 3), (30, 0), (30, 1001)]
)
def test_invalid_polling_parameters_fail_before_browser(
    monkeypatch, tmp_path, interval, samples
):
    monkeypatch.setattr(
        observer, "run", lambda _args: pytest.fail("browser must not launch")
    )
    with pytest.raises(SystemExit) as exc:
        observer.main(
            [
                "--expected",
                "RUN-001",
                "--selector",
                "[data-status]",
                "--browser-profile",
                str(tmp_path / "account-b"),
                "--refresh-seconds",
                str(interval),
                "--samples",
                str(samples),
            ]
        )
    assert exc.value.code == 2


def test_missing_browser_executable_fails_before_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(
        observer, "run", lambda _args: pytest.fail("browser must not launch")
    )
    with pytest.raises(SystemExit) as exc:
        observer.main(
            [
                "--expected",
                "RUN-001",
                "--browser-profile",
                str(tmp_path / "account-b"),
                "--browser-executable",
                str(tmp_path / "absent-chrome"),
            ]
        )
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "selector",
    [
        "body",
        "html",
        "#profiles-pages",
        ".nav-popover-username",
        ".nickname-info",
    ],
)
def test_generic_profile_selectors_are_not_status_evidence(selector):
    page = FakePage(observer.profile_url("Seaza"), [FakeNode("Seaza")])
    with pytest.raises(ValueError, match="not status evidence"):
        observer.observe(page, "Seaza", "Seaza", selector)


def test_profile_banner_exact_text_without_hardcoded_css():
    class ProfileContent:
        def get_by_text(self, value, exact=False):
            assert value == ", but every challenge brings"
            assert exact is True
            return FakeLocator([FakeNode(value)])

    class BrowserPage:
        url = observer.profile_url("Seaza")

        def locator(self, selector):
            assert selector == "#content-inner"
            return ProfileContent()

    result = observer.observe(
        BrowserPage(), "Seaza", ", but every challenge brings", ""
    )
    assert result["state"] == "WEB_PROFILE_MATCH"


# ---------- Evidence bundle (summary.json / manifest.json / linkage fields) ----------


def _base_result(samples, matching, top_states, first=None, last=None):
    return {
        "run_id": "run0123456789abcdef",
        "nickname": "Seaza",
        "source": "authenticated_web_profile",
        "command_id": "cmd-1",
        "account_b_reference": "acctb-9",
        "observed_at": "t0",
        "finished_at": "t1",
        "expected_text_sha256": "e" * 64,
        "first_observed_text_sha256": first,
        "last_observed_text_sha256": last,
        "top_states": top_states,
        "matching_samples": matching,
        "samples": samples,
        "sample_log": "run.jsonl",
        "screenshot_files": ["a.png"],
    }


def test_summary_never_claims_external_effect_or_worker_stages():
    summary = observer.build_summary(
        _base_result(10, 10, {"WEB_PROFILE_MATCH": 10}, first="a" * 64, last="b" * 64),
        "run.jsonl",
    )
    chain = summary["stage_chain"]
    assert chain["WEB_PROFILE_OBSERVED"] is True
    assert chain["EXTERNAL_EFFECT_VERIFIED"] is False
    assert chain["COMMAND_ACCEPTED"] == observer.STAGE_EXTERNAL_TO_OBSERVER
    assert chain["WORKER_EXECUTED"] == observer.STAGE_EXTERNAL_TO_OBSERVER
    assert summary["transition_detected"] is True
    assert summary["web_profile_observed"] is True


def test_summary_without_matching_samples_reports_web_not_observed():
    summary = observer.build_summary(
        _base_result(
            10, 0, {"WEB_PROFILE_MISMATCH": 10}, first="a" * 64, last="a" * 64
        ),
        "run.jsonl",
    )
    assert summary["stage_chain"]["WEB_PROFILE_OBSERVED"] is False
    assert summary["stage_chain"]["EXTERNAL_EFFECT_VERIFIED"] is False
    assert summary["web_profile_observed"] is False
    assert summary["transition_detected"] is False


def test_optional_reference_validation():
    assert observer._validate_optional_text(None, 64, "command_id") == ""
    assert observer._validate_optional_text("", 64, "command_id") == ""
    assert observer._validate_optional_text("cmd-001", 64, "command_id") == "cmd-001"
    with pytest.raises(ValueError):
        observer._validate_optional_text("a\nb", 64, "command_id")
    with pytest.raises(ValueError):
        observer._validate_optional_text(
            "x" * 65, observer.MAX_COMMAND_ID, "command_id"
        )
    with pytest.raises(ValueError):
        observer._validate_optional_text(
            "x" * 129, observer.MAX_ACCOUNT_B_REFERENCE, "account_b_reference"
        )


def test_manifest_hashes_every_evidence_file(tmp_path):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run-0001.png").write_bytes(b"png-bytes")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("x", encoding="utf-8")
    manifest = observer.write_manifest(tmp_path, "run1", "now")

    names = [entry["file"] for entry in manifest["files"]]
    assert names == ["run-0001.png", "summary.json"]
    summary_entry = manifest["files"][1]
    assert summary_entry["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert summary_entry["size_bytes"] == 2
    assert manifest["run_id"] == "run1" and manifest["generated_at"] == "now"


def test_cli_propagates_optional_linkage_fields(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args):
        captured["command_id"] = args.command_id
        captured["account_b_reference"] = args.account_b_reference
        return 0

    monkeypatch.setattr(observer, "run", fake_run)
    assert (
        observer.main(
            [
                "--expected",
                "RUN-001",
                "--selector",
                "[data-status]",
                "--browser-profile",
                str(tmp_path / "account-b"),
                "--command-id",
                "cmd-42",
                "--account-b-reference",
                "oper-opaque-ref",
            ]
        )
        == 0
    )
    assert captured == {
        "command_id": "cmd-42",
        "account_b_reference": "oper-opaque-ref",
    }
