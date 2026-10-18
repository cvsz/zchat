"""Evidence-bounded capability registry for ZChat's packaged applications."""

from __future__ import annotations

from copy import deepcopy

_BASE_CAPABILITIES = {
    "standalone_supported": True,
    "supports_offline": False,
    "supports_read_only": False,
    "supports_strict_background": False,
    "supports_minimized_client": False,
    "supports_hidden_client": False,
    "supports_remote_commands": False,
    "supports_external_outcome_verification": False,
    "requires_exclusive_resource": True,
    "verified_platforms": [],
    "verified_provider_versions": [],
    "implementation_status": "implemented",
    "evidence_status": "PARTIALLY VERIFIED",
}

_FEATURES = (
    ("camfrog.room_control", "camfrog", True, False),
    ("camfrog.private_chat", "camfrog", True, True),
    # Both compatibility features are first-class modes in the embedded shared
    # Status Changer module and therefore run inside zchat-client.exe.
    ("camfrog.status_random", "camfrog", True, True),
    ("camfrog.status_marquee", "camfrog", True, True),
    ("camfrog.im_autoreply", "camfrog", True, False),
    ("camfrog.web_status", "camfrog", False, False),
    ("camfrog.music_dj", "camfrog", True, False),
    ("camfrog.status_changer", "camfrog", True, True),
    ("line.status_changer", "line", True, False),
)


def get_feature_capabilities() -> list[dict[str, object]]:
    """Return defensive copies of the current feature capability matrix.

    Standalone support means a dedicated source entry point and build target
    exist. It does not assert that the executable passed a live Windows/provider
    acceptance test. Unified support is true only for an explicitly embedded
    shared module; remote mutation and live-verification claims remain false.
    """
    result = []
    for feature_id, provider, requires_provider_client, unified_supported in _FEATURES:
        capability = deepcopy(_BASE_CAPABILITIES)
        capability.update(
            {
                "feature_id": feature_id,
                "provider": provider,
                "requires_provider_client": requires_provider_client,
                "unified_supported": unified_supported,
            }
        )
        if feature_id == "camfrog.private_chat":
            capability["supports_read_only"] = True
        result.append(capability)
    return result
