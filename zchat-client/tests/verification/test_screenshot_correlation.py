from pathlib import Path

from zchat_client.verification.screenshot_correlation import correlate_screenshot


def test_correlate_screenshot():
    event = {"event": "friend_status_observed", "result": "PASS"}
    sc = Path("evidence/run-1/screenshots/after.png")
    out = correlate_screenshot(event, sc)
    assert out["screenshot_id"] == "after"
    assert out["screenshot_path"] == str(sc)
    assert out["event"] == "friend_status_observed"
    assert out["result"] == "PASS"
