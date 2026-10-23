"""Launch the combined Random/Marquee Status Changer application."""

import os
import sys
from pathlib import Path

os.environ["CAMFROG_APP_PROFILE"] = "status"

_SHARED_DIR = Path(__file__).resolve().parent.parent / "camfrog"
if _SHARED_DIR.is_dir():
    sys.path.insert(0, str(_SHARED_DIR))

from status_changer_gui import main


if __name__ == "__main__":
    sys.exit(main())
