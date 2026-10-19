from __future__ import annotations

from pathlib import Path

from camfrog_control_server.feature_capabilities import get_feature_capabilities
from camfrog_control_server.schemas import ZChatFeatureCapability

ROOT = Path(__file__).resolve().parents[2]

FEATURE_BUILD_TARGETS = {
    "camfrog.room_control": (
        "src/features/room_control_entry.py",
        "build.bat",
        "src/features/room_control_entry.py",
        "room-control",
    ),
    "camfrog.private_chat": (
        "src/features/chat_im_private_entry.py",
        "build.bat",
        "src/features/chat_im_private_entry.py",
        "chat-im-private",
    ),
    "camfrog.status_random": (
        "src/features/status_random_entry.py",
        "build.bat",
        "src/features/status_random_entry.py",
        "status-random",
    ),
    "camfrog.status_marquee": (
        "src/features/status_marquee_entry.py",
        "build.bat",
        "src/features/status_marquee_entry.py",
        "status-marquee",
    ),
    "camfrog.im_autoreply": (
        "src/features/im_autoreply_entry.py",
        "build.bat",
        "src/features/im_autoreply_entry.py",
        "im-autoreply",
    ),
    "camfrog.web_status": (
        "src/features/web_status_entry.py",
        "build.bat",
        "src/features/web_status_entry.py",
        "web-status",
    ),
    "camfrog.music_dj": (
        "src/features/music_dj_entry.py",
        "build.bat",
        "src/features/music_dj_entry.py",
        "music-dj",
    ),
    "camfrog.status_changer": (
        "src/features/status_changer_entry.py",
        "build.bat",
        "src/features/status_changer_entry.py",
        "status-changer",
    ),
    "line.status_changer": (
        "line/line_status_gui.py",
        "line/build.bat",
        "line_status_gui.py",
        "line-status-changer",
    ),
}


def test_capability_matrix_matches_every_standalone_build_target() -> None:
    capabilities = get_feature_capabilities()
    by_id = {item["feature_id"]: item for item in capabilities}

    assert set(by_id) == set(FEATURE_BUILD_TARGETS)
    assert len(capabilities) == len(by_id)

    for feature_id, (
        entrypoint,
        build_script,
        build_argument,
        executable_name,
    ) in FEATURE_BUILD_TARGETS.items():
        capability = by_id[feature_id]
        ZChatFeatureCapability.model_validate(capability)
        assert capability["standalone_supported"] is True
        assert capability["unified_supported"] is (
            feature_id
            in {
                "camfrog.status_random",
                "camfrog.status_marquee",
                "camfrog.status_changer",
                "camfrog.private_chat",
            }
        )
        if feature_id == "camfrog.private_chat":
            assert capability["supports_read_only"] is True
        assert capability["supports_remote_commands"] is False
        assert capability["supports_external_outcome_verification"] is False
        assert capability["supports_strict_background"] is False
        assert capability["supports_minimized_client"] is False
        assert capability["supports_hidden_client"] is False
        assert capability["verified_platforms"] == []
        assert capability["verified_provider_versions"] == []
        assert capability["evidence_status"] == "PARTIALLY VERIFIED"

        source_path = ROOT / entrypoint
        script_path = ROOT / build_script
        assert source_path.is_file(), f"missing source entry point for {feature_id}"
        script = script_path.read_text(encoding="utf-8").replace("/", "\\").lower()
        build_argument = build_argument.replace("/", "\\").lower()
        command_blocks = script.split("-m pyinstaller")
        assert any(
            f"--name {executable_name}" in block and build_argument in block
            for block in command_blocks
        ), f"missing dedicated PyInstaller command for {feature_id}"


def test_capability_results_are_defensive_copies() -> None:
    first = get_feature_capabilities()
    first[0]["verified_platforms"].append("unverified")

    assert get_feature_capabilities()[0]["verified_platforms"] == []
