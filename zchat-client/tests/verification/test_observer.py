import platform

from zchat_client.verification.observer import FriendListObserver, Observation


def test_missing_target_returns_not_visible():
    obs = FriendListObserver("").observe()
    assert obs.visible is False
    assert obs.confidence == "low"


def test_custom_reader_used():
    expected = Observation(True, "Testing", "Name", "high", "fake_uia")
    obs = FriendListObserver("acct", reader=lambda t: expected).observe()
    assert obs == expected


def test_invalid_reader_result_handled():
    obs = FriendListObserver("acct", reader=lambda t: "bad").observe()
    assert obs.visible is False
    assert obs.confidence == "low"


def test_unsupported_platform_without_reader():
    if platform.system() == "Windows":
        return
    obs = FriendListObserver("acct").observe()
    assert obs.visible is False
    assert obs.method == "unsupported_platform"
