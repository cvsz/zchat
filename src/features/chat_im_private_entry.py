"""Launch the private-chat window manager."""
import os
import sys
from pathlib import Path

os.environ["CAMFROG_APP_DATA_DIR"] = str(
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else
    Path(__file__).resolve().parent / "chat-im-private-data")

_SHARED_DIR = Path(__file__).resolve().parent.parent / "camfrog"
if _SHARED_DIR.is_dir():
    sys.path.insert(0, str(_SHARED_DIR))

from chat_im_private_gui import main


if __name__ == "__main__":
    sys.exit(main())
