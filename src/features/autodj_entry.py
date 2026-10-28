"""Launch the AutoDJ application (modular architecture)."""
import os
import sys
from pathlib import Path

os.environ["CAMFROG_APP_DATA_DIR"] = str(
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else
    Path(__file__).resolve().parent / "autodj-data")

# Add src/ to path so we can import camfrog_autodj and camfrog
_SRC_ROOT = Path(__file__).resolve().parent.parent
if _SRC_ROOT.is_dir():
    sys.path.insert(0, str(_SRC_ROOT))

from camfrog_autodj import main


if __name__ == "__main__":
    sys.exit(main())