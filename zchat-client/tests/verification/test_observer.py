import platform
from types import SimpleNamespace

from zchat_client.verification.observer import (
    FriendListObserver,
    Observation,
    _discover_row_status,
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
    # Low confidence from the reader is fail-closed to not-visible: seeing a
    # friend row without a readable status field is not external verification.
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
    # High confidence but no status: friend seen, external status not confirmed.
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


def test_is_verified_camfrog_process_name_only():
    proc = SimpleNamespace(name=lambda: "camfrog.exe", exe=lambda: "")
    assert _is_verified_camfrog_process(proc) is True


def test_is_verified_camfrog_process_camfrog_path():
    proc = SimpleNamespace(
        name=lambda: "camfrog.exe", exe=lambda: "C:\\\\Camfrog\\\\camfrog.exe"
    )
    assert _is_verified_camfrog_process(proc) is True


def test_is_verified_camfrog_process_wrong_name():
    proc = SimpleNamespace(
        name=lambda: "notcamfrog.exe", exe=lambda: "C:\\\\notcamfrog\\\\foo.exe"
    )
    assert _is_verified_camfrog_process(proc) is False


def test_is_verified_camfrog_process_non_camfrog_exe_path():
    proc = SimpleNamespace(
        name=lambda: "camfrog.exe", exe=lambda: "C:\\\\OtherApp\\\\foo.exe"
    )
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


def test_read_control_value_via_value_pattern():
    pattern = SimpleNamespace(Value="Hello from Camfrog")
    control = SimpleNamespace(get_value_pattern=lambda: pattern)
    assert _read_control_value(control) == "Hello from Camfrog"


def test_read_control_value_via_legacy_iaccessible():
    pattern = SimpleNamespace(Value="Legacy status text")
    control = SimpleNamespace(
        get_value_pattern=lambda: None,
        get_legacy_iaccessible_pattern=lambda: pattern,
    )
    assert _read_control_value(control) == "Legacy status text"


def test_read_control_value_no_pattern_returns_none():
    control = SimpleNamespace(
        get_value_pattern=lambda: None,
        get_legacy_iaccessible_pattern=lambda: None,
    )
    assert _read_control_value(control) is None


def test_read_control_value_pattern_raises_returns_none():
    control = SimpleNamespace(
        get_value_pattern=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert _read_control_value(control) is None


def test_discover_row_status_single_candidate_returns_value():
    ctrl = SimpleNamespace(
        get_value_pattern=lambda: SimpleNamespace(Value="Custom status")
    )
    row = SimpleNamespace(descendants=lambda: [ctrl])
    assert _discover_row_status(row) == "Custom status"


def test_discover_row_status_no_candidates_returns_none():
    ctrl = SimpleNamespace(get_value_pattern=lambda: None)
    row = SimpleNamespace(descendants=lambda: [ctrl])
    assert _discover_row_status(row) is None


def test_discover_row_status_multiple_candidates_returns_none():
    c1 = SimpleNamespace(get_value_pattern=lambda: SimpleNamespace(Value="One"))
    c2 = SimpleNamespace(get_value_pattern=lambda: SimpleNamespace(Value="Two"))
    row = SimpleNamespace(descendants=lambda: [c1, c2])
    assert _discover_row_status(row) is None


def test_discover_row_status_descendants_raises_returns_none():
    row = SimpleNamespace(
        descendants=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert _discover_row_status(row) is None
