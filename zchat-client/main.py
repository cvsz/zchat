"""Launch the ZChat agent chat desktop client from source or a packaged build."""

from __future__ import annotations

import sys
from pathlib import Path


def _configure_source_paths() -> None:
    root = Path(__file__).resolve().parent
    candidates = (
        root / "src",
        root.parent / "control-server" / "src",
        root.parent / "src" / "features",
        # Shared runtime modules (camfrog_windows_runtime, ...).
        # Standalone and unified builds package the same 1:1 sources.
        root.parent / "src" / "camfrog",
    )
    for candidate in reversed(candidates):
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))


def main() -> None:
    _configure_source_paths()
    if "--worker" in sys.argv[1:]:
        from status_changer_gui import main as run_status_worker

        raise SystemExit(run_status_worker(sys.argv[1:]))
    if "--classic" in sys.argv:
        from zchat_client.ui import main as launch_ui

        launch_ui()
        return
    from zchat_client.ui import main_unified as launch_unified

    launch_unified()


if __name__ == "__main__":
    main()
