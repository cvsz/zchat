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
    "EvidenceTransport",
    "FriendListObserver",
    "NullScreenshotCapture",
    "Observation",
    "ScreenshotCapture",
    "TransportResult",
    "VerificationConfig",
    "VerificationLogger",
    "VerificationResult",
    "VerificationRun",
    "VerificationState",
    "VerificationStateMachine",
    "__version__",
    "create_run_dir",
    "load_config",
    "normalize_text",
    "record_request",
    "sync_keep_log",
    "texts_equal",
    "write_checksums",
    "write_manifest",
    "write_report",
]

__version__ = "0.1.0"
