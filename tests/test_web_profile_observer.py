"""Offline browser-observer contract tests; no Camfrog account required."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_profile_observer", ROOT / "tools" / "web_profile_observer.py")
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


@pytest.mark.parametrize("url", [
    "https://profiles.camfrog.com.evil.test/th/zdevz",
    "http://profiles.camfrog.com/th/zdevz",
    "https://evil.test/th/zdevz",
    "https://profiles.camfrog.com/th/another",
    "https://attacker@profiles.camfrog.com/th/zdevz",
])
def test_observer_rejects_unexpected_location(url):
    result = observer.observe(FakePage(url, [FakeNode("HELLO")]), "zdevz", "HELLO", "[data-status]")
    assert result["state"] == "UNVERIFIED"


def test_exact_visible_match_is_only_web_evidence():
    result = observer.observe(
        FakePage(observer.profile_url("zdevz"), [FakeNode("RUN-001")]),
        "zdevz", "RUN-001", "[data-status]")
    assert result["state"] == "WEB_PROFILE_MATCH"
    assert result["observed_text_sha256"] == result["expected_text_sha256"]


def test_mismatch_does_not_report_success():
    result = observer.observe(
        FakePage(observer.profile_url("zdevz"), [FakeNode("old status")]),
        "zdevz", "RUN-001", "[data-status]")
    assert result["state"] == "WEB_PROFILE_MISMATCH"


@pytest.mark.parametrize("nodes,reason", [
    ([], "selector_not_unique"),
    ([FakeNode("a"), FakeNode("b")], "selector_not_unique"),
    ([FakeNode("RUN-001", visible=False)], "status_element_not_visible"),
    ([FakeNode("  ")], "empty_status_element"),
])
def test_non_verifiable_cases_fail_closed(nodes, reason):
    result = observer.observe(
        FakePage(observer.profile_url("zdevz"), nodes),
        "zdevz", "RUN-001", "[data-status]")
    assert result == {"state": "UNVERIFIED", "reason": reason} or result["reason"] == reason


def test_invalid_inputs():
    page = FakePage(observer.profile_url("zdevz"), [FakeNode("RUN-001")])
    for expected, selector in [("", "[data-status]"), ("X" * 257, "[data-status]"),
                               ("good\nbad", "[data-status]"), ("good", "")]:
        with pytest.raises(ValueError):
            observer.observe(page, "zdevz", expected, selector)
