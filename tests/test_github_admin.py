"""Safety and payload tests for the opt-in GitHub administration helper."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import github_admin as admin  # noqa: E402


def test_dry_run_does_not_require_github_authentication(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["github_admin.py", "--repo", "OWNER/REPO", "--branch", "main"]
    )

    def unexpected_auth():
        raise AssertionError("dry-run must not require an authenticated gh CLI")

    monkeypatch.setattr(admin, "require_gh", unexpected_auth)
    assert admin.main() == 0
    assert "Dry-run only" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["github_admin.py", "--repo", "OWNER/REPO", "--branch", "main", "--apply"],
        ["github_admin.py", "--repo", "OWNER/REPO", "--branch", "main", "--verify"],
    ],
)
def test_mutation_and_verification_require_explicit_check_names(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        admin.main()
    assert exc.value.code == 2


def test_protection_payload_uses_only_operator_supplied_checks():
    payload = admin.protection_payload(("ci / test", "CodeQL / Analyze"))
    assert payload["required_status_checks"]["contexts"] == [
        "ci / test",
        "CodeQL / Analyze",
    ]


def test_apply_encodes_branch_slashes_and_targets_explicit_repo(monkeypatch):
    calls = []
    monkeypatch.setattr(admin, "gh_api", lambda *args: calls.append(args) or {})

    admin.apply("OWNER/REPO", "release/stable", ("ci / test",))

    assert calls[0][0:2] == (
        "PUT",
        "repos/OWNER/REPO/branches/release%2Fstable/protection",
    )
    assert calls[0][2]["required_status_checks"]["contexts"] == ["ci / test"]


@pytest.mark.parametrize(
    "repo", ["", "OWNER", "OWNER/REPO/EXTRA", "OWNER/../REPO", "OWNER/REPO?x=1"]
)
def test_invalid_repository_targets_are_rejected(repo):
    with pytest.raises(ValueError, match="OWNER/REPOSITORY"):
        admin.validate_target(repo, "main", ())


def test_invalid_check_names_are_rejected():
    with pytest.raises(ValueError, match="single-line"):
        admin.validate_target("OWNER/REPO", "main", ("good", "bad\ncheck"))
