from __future__ import annotations

import pytest

from tools import require_non_admin


@pytest.mark.parametrize("elevation_type", [1, 3])
def test_build_guard_allows_non_elevated_windows_tokens(
    monkeypatch: pytest.MonkeyPatch,
    elevation_type: int,
) -> None:
    monkeypatch.setattr(require_non_admin, "_token_elevation_type", lambda: elevation_type)

    assert require_non_admin.main() == 0


def test_build_guard_rejects_a_fully_elevated_windows_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(require_non_admin, "_token_elevation_type", lambda: 2)

    assert require_non_admin.main() == 1
    assert "non-elevated terminal" in capsys.readouterr().err


def test_build_guard_fails_closed_when_token_elevation_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_to_read_token() -> int:
        raise OSError("token query failed")

    monkeypatch.setattr(require_non_admin, "_token_elevation_type", fail_to_read_token)

    assert require_non_admin.main() == 1
    assert "Could not verify Windows token elevation" in capsys.readouterr().err
