"""Launch standalone Name Changer: GUI or CLI passthrough."""

import os
import sys
from pathlib import Path

os.environ["CAMFROG_APP_PROFILE"] = "name-changer"

_SHARED_DIR = Path(__file__).resolve().parent.parent / "camfrog"
if _SHARED_DIR.is_dir():
    sys.path.insert(0, str(_SHARED_DIR))

from name_changer_gui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
