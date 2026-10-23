import platform
from types import SimpleNamespace

import pytest
from zchat_client.verification.observer import (
    FriendListObserver,
    Observation,
    _discover_row_status,
    _is_custom_status_control,
    _is_verified_camfrog_process,
    _process_identity,
    _read_control_value,
)


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


def test_reader_result_with_status_passes_through_all_fields():
    expected = Observation(
        True,
        "Hello world",
        "Nutch",
        "high",
        "uia_status_field",
        account_identity="nutch",
        process_identity={
            "pid": "1234",
            "name": "camfrog.exe",
            "exe": "C:\\\\Camfrog\\\\camfrog.exe",
        },
        screenshot=None,
        error_reason=None,
    )
    obs = FriendListObserver("nutch", reader=lambda t: expected).observe()
    assert obs == expected


def test_reader_result_without_status_is_low_confidence_identity_only():
    reader_result = Observation(
        True,
        None,
        "Nutch",
        "low",
        "uia_identity_only",
        account_identity="nutch",
        process_identity={"pid": "1234"},
        error_reason="status_field_not_accessible",
    )
    obs = FriendListObserver("nutch", reader=lambda t: reader_result).observe()
    assert obs.visible is False
    assert obs.status is None
    assert obs.confidence == "low"
    assert obs.method == "uia_identity_only"
    assert obs.error_reason == "status_field_not_accessible"


def test_reader_result_high_confidence_without_status_is_identity_only_visible():
    reader_result = Observation(
        True,
        None,
        "Nutch",
        "high",
        "uia_identity_only",
        account_identity="nutch",
        process_identity={"pid": "1234"},
        error_reason="status_field_not_accessible",
    )
    obs = FriendListObserver("nutch", reader=lambda t: reader_result).observe()
    assert obs.visible is True
    assert obs.status is None
    assert obs.confidence == "low"
    assert obs.method == "uia_identity_only"
    assert obs.error_reason == "status_field_not_accessible"


# ---------- Finding 1: real pywinauto UIA accessors (0.6.9) ----------


def test_read_control_value_via_wrapper_get_value():
    control = SimpleNamespace(get_value=lambda: "Hello from Camfrog")
    assert _read_control_value(control) == "Hello from Camfrog"


@pytest.mark.skipif(
    platform.system() != "Windows",
    reason="raw AutomationElement pattern access requires Windows COM",
)
def test_read_control_value_via_element_get_current_pattern():
    pattern = SimpleNamespace(Current=SimpleNamespace(Value="Raw element status"))
    element = SimpleNamespace(GetCurrentPattern=lambda ptrn_id: pattern)
    control = SimpleNamespace(element=element)
    assert _read_control_value(control) == "Raw element status"


def test_read_control_value_wrapper_raises_returns_none():
    control = SimpleNamespace(get_value=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _read_control_value(control) is None


def test_read_control_value_element_raises_returns_none():
    control = SimpleNamespace(
        element=SimpleNamespace(
            GetCurrentPattern=lambda ptrn_id: (_ for _ in ()).throw(RuntimeError("boom"))
        )
    )
    assert _read_control_value(control) is None


def test_read_control_value_no_accessors_returns_none():
    assert _read_control_value(SimpleNamespace()) is None


def test_read_control_value_empty_string_returns_none():
    assert _read_control_value(SimpleNamespace(get_value=lambda: "")) is None


def test_read_control_value_whitespace_only_returns_empty():
    assert _read_control_value(SimpleNamespace(get_value=lambda: "   ")) == ""


# ---------- Process identity (unchanged behavior) ----------


def test_is_verified_camfrog_process_name_only():
    proc = SimpleNamespace(name=lambda: "camfrog.exe", exe=lambda: "")
    assert _is_verified_camfrog_process(proc) is True


def test_is_verified_camfrog_process_camfrog_path():
    proc = SimpleNamespace(name=lambda: "camfrog.exe", exe=lambda: "C:\\\\Camfrog\\\\camfrog.exe")
    assert _is_verified_camfrog_process(proc) is True


def test_is_verified_camfrog_process_wrong_name():
    proc = SimpleNamespace(name=lambda: "notcamfrog.exe", exe=lambda: "C:\\\\notcamfrog\\\\foo.exe")
    assert _is_verified_camfrog_process(proc) is False


def test_is_verified_camfrog_process_non_camfrog_exe_path():
    proc = SimpleNamespace(name=lambda: "camfrog.exe", exe=lambda: "C:\\\\OtherApp\\\\foo.exe")
    assert _is_verified_camfrog_process(proc) is False


def test_is_verified_camfrog_process_access_denied():
    proc = SimpleNamespace(
        name=lambda: (_ for _ in ()).throw(OSError("access denied")), exe=lambda: ""
    )
    assert _is_verified_camfrog_process(proc) is False


def test_process_identity_contains_pid_name_exe():
    proc = SimpleNamespace(
        pid=1234, name=lambda: "camfrog.exe", exe=lambda: "C:\\\\Camfrog\\\\camfrog.exe"
    )
    ident = _process_identity(proc)
    assert ident["pid"] == "1234"
    assert ident["name"] == "camfrog.exe"
    assert ident["exe"] == "C:\\\\Camfrog\\\\camfrog.exe"


# ---------- Finding 2: positive Custom Status field identification ----------


def _status_control(**overrides):
    base = SimpleNamespace(
        control_type="Text",
        enabled=True,
        visible=True,
        name="Custom status text",
        automation_id="",
    )
    base.__dict__.update(overrides)
    if "get_value" not in overrides:
        base.get_value = lambda: base.name
    return base


def test_is_custom_status_control_text_visible_enabled():
    assert _is_custom_status_control(_status_control()) is True


def test_is_custom_status_control_edit_type_accepted():
    assert _is_custom_status_control(_status_control(control_type="Edit")) is True


def test_is_custom_status_control_wrong_control_type_rejected():
    assert _is_custom_status_control(_status_control(control_type="Button")) is False
    assert _is_custom_status_control(_status_control(control_type="ListItem")) is False


def test_is_custom_status_control_disabled_rejected():
    assert _is_custom_status_control(_status_control(enabled=False)) is False


def test_is_custom_status_control_hidden_rejected():
    assert _is_custom_status_control(_status_control(visible=False)) is False


def test_is_custom_status_control_empty_name_rejected():
    assert _is_custom_status_control(_status_control(name="")) is False


def test_is_custom_status_control_nickname_hint_rejected():
    assert _is_custom_status_control(_status_control(name="Nickname")) is False
    assert _is_custom_status_control(_status_control(name="Nutch Nick")) is False


def test_is_custom_status_control_presence_hint_rejected():
    assert _is_custom_status_control(_status_control(name="Presence")) is False
    assert _is_custom_status_control(_status_control(automation_id="presenceIcon")) is False


def test_is_custom_status_control_flair_hint_rejected():
    assert _is_custom_status_control(_status_control(name="Profile Flair")) is False


def test_is_custom_status_control_avatar_hint_rejected():
    assert _is_custom_status_control(_status_control(name="Avatar")) is False


def test_is_custom_status_control_tooltip_hint_rejected():
    assert _is_custom_status_control(_status_control(name="Tooltip")) is False


def test_is_custom_status_control_mood_hint_rejected():
    assert _is_custom_status_control(_status_control(name="Mood")) is False


def test_is_custom_status_control_access_denied_returns_false():
    class ThrowingControl:
        @property
        def control_type(self):
            raise OSError("boom")

    assert _is_custom_status_control(ThrowingControl()) is False


def test_discover_row_status_readable_nickname_but_no_status_returns_none():
    nickname = _status_control(name="Nickname", automation_id="nickName")
    flair = _status_control(name="Profile Flair")
    row = SimpleNamespace(descendants=lambda: [nickname, flair])
    assert _discover_row_status(row) is None


def test_discover_row_status_readable_presence_but_no_status_returns_none():
    presence = _status_control(name="Presence", automation_id="presenceIcon")
    row = SimpleNamespace(descendants=lambda: [presence])
    assert _discover_row_status(row) is None


def test_discover_row_status_exactly_one_status_returns_value():
    status = _status_control(name="Hello world", automation_id="statusValue")
    nickname = _status_control(name="Nickname")
    row = SimpleNamespace(descendants=lambda: [nickname, status])
    assert _discover_row_status(row) == "Hello world"


def test_discover_row_status_two_status_candidates_returns_none():
    s1 = _status_control(name="Status one")
    s2 = _status_control(name="Status two")
    row = SimpleNamespace(descendants=lambda: [s1, s2])
    assert _discover_row_status(row) is None


def test_discover_row_status_descendants_raises_returns_none():
    row = SimpleNamespace(descendants=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _discover_row_status(row) is None


def test_discover_row_status_control_value_raises_skipped():
    bad_ctrl = _status_control(name="Bad value")
    del bad_ctrl.get_value  # path 1 raises AttributeError; path 2 has no element
    good_ctrl = _status_control(name="Good status", automation_id="statusValue")
    row = SimpleNamespace(descendants=lambda: [bad_ctrl, good_ctrl])
    assert _discover_row_status(row) == "Good status"
