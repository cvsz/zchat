"""Launch the stand-alone CloudBot Manager window (Camfrog cloudbot only).

This entry runs the same Bot Manager page that web-status.exe embeds, but in a
window that contains only that page: the web-status and account tabs stay out
of the interface while the underlying module remains shared at build time.
Data stays next to each executable, so web-status.exe and
CloudBot-Manager.exe keep separate installation folders in practice.
"""
import os

os.environ["ZCHAT_BOT_MANAGER_ONLY"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_SHARED_DIR = Path(__file__).resolve().parent.parent / "camfrog"
if _SHARED_DIR.is_dir():
    sys.path.insert(0, str(_SHARED_DIR))

from web_status_gui import main


if __name__ == "__main__":
    raise SystemExit(main())
