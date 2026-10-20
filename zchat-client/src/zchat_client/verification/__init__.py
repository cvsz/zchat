"""Second-account verification and evidence collection package."""

from .comparison import normalize_text, texts_equal
from .config import VerificationConfig, load_config
from .evidence import create_run_dir, write_checksums, write_manifest
from .integration import VerificationRun, record_request
from .logger import VerificationLogger
from .observer import FriendListObserver, Observation
from .report import write_report
from .screenshot import NullScreenshotCapture, ScreenshotCapture
from .state_machine import (
    VerificationResult,
    VerificationState,
    VerificationStateMachine,
)
from .sync import sync_keep_log
from .transport import EvidenceTransport, TransportResult

__all__ = [
    "__version__",
    "VerificationConfig",
    "load_config",
    "VerificationLogger",
    "VerificationResult",
    "VerificationState",
    "VerificationStateMachine",
]

__version__ = "0.1.0"
