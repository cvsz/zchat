"""Read-only, locally authenticated Camfrog web-profile observer.

Web text equality is a browser observation, not proof of a Camfrog desktop
Friend List status or an external side effect. No cookies are exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

PROFILE_ORIGIN = "https://profiles.camfrog.com"
MAX_EXPECTED = 256
MAX_SELECTOR = 256


def profile_url(nickname: str) -> str:
    if not isinstance(nickname, str) or not 1 <= len(nickname) <= 128:
        raise ValueError("Nickname length must be 1..128")
    if any(ord(c) < 33 or ord(c) == 127 or c in "/?#\\@" for c in nickname):
        raise ValueError("Nickname contains unsafe characters")
    return f"{PROFILE_ORIGIN}/th/{quote(nickname, safe='')}"


def allowed_url(value: str, nickname: str) -> bool:
    try:
        p = urlsplit(value)
        return (p.scheme == "https" and p.netloc == "profiles.camfrog.com"
                and p.path.rstrip("/") == urlsplit(profile_url(nickname)).path
                and not p.username and not p.password)
    except (ValueError, TypeError):
        return False


def observe(page, nickname: str, expected: str, selector: str) -> dict:
    """Match a caller-confirmed visible element; never infer desktop success."""
    if not isinstance(expected, str) or not 1 <= len(expected) <= MAX_EXPECTED:
        raise ValueError("Expected status must be 1..256 characters")
    if any(ord(c) < 32 for c in expected):
        raise ValueError("Expected status contains control characters")
    if not selector or len(selector) > MAX_SELECTOR:
        raise ValueError("A bounded, explicitly inspected CSS selector is required")
    if not allowed_url(page.url, nickname):
        return {"state": "UNVERIFIED", "reason": "unexpected_profile_url"}
    nodes = page.locator(selector)
    count = nodes.count()
    if count != 1:
        return {"state": "UNVERIFIED", "reason": "selector_not_unique", "matched_nodes": count}
    node = nodes.first
    if not node.is_visible():
        return {"state": "UNVERIFIED", "reason": "status_element_not_visible"}
    actual = node.inner_text(timeout=5000).strip()
    if not actual:
        return {"state": "UNVERIFIED", "reason": "empty_status_element"}
    matched = actual == expected
    return {"state": "WEB_PROFILE_MATCH" if matched else "WEB_PROFILE_MISMATCH",
            "reason": "visible_element_text_comparison",
            "observed_text_sha256": hashlib.sha256(actual.encode("utf-8")).hexdigest(),
            "expected_text_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest()}


def run(args) -> int:
    from playwright.sync_api import sync_playwright

    url = profile_url(args.nickname)
    output = Path(args.output).expanduser().resolve()
    profile = Path(args.browser_profile).expanduser().resolve()
    if output == profile or output in profile.parents or profile in output.parents:
        raise ValueError("Evidence directory and browser profile must be separate")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        output.chmod(0o700)
    result = {"run_id": uuid4().hex, "observed_at": datetime.now(timezone.utc).isoformat(),
              "nickname": args.nickname, "source": "authenticated_web_profile",
              "desktop_status_verified": False, "state": "UNVERIFIED"}
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel="chrome", headless=args.headless,
            accept_downloads=False, service_workers="block")
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if args.login:
                print("Complete Account B login in Chrome, then press Enter.", file=sys.stderr)
                input()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            result.update(observe(page, args.nickname, args.expected, args.selector))
            screenshot = output / f"{result['run_id']}.png"
            page.screenshot(path=str(screenshot), full_page=False)
            if os.name != "nt":
                screenshot.chmod(0o600)
            result["screenshot_sha256"] = hashlib.sha256(screenshot.read_bytes()).hexdigest()
            result["screenshot_file"] = screenshot.name
        finally:
            context.close()
    evidence = output / f"{result['run_id']}.json"
    evidence.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if os.name != "nt":
        evidence.chmod(0o600)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["state"] == "WEB_PROFILE_MATCH" else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nickname", required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--selector", required=True,
                        help="Unique CSS selector manually validated on Account B profile")
    parser.add_argument("--browser-profile", required=True,
                        help="Dedicated local Chrome profile; sign in interactively")
    parser.add_argument("--output", default="reports/web-profile-observer")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--login", action="store_true", help="Pause for interactive login")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(f"UNVERIFIED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
