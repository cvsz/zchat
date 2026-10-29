__version__ = "0.2.4"

from .__main__ import main
from .single_instance import (
    SingleInstance,
    CrashReporter,
    setup_single_instance_and_crash_reporting,
    get_instance_id,
)

__all__ = [
    "main",
    "__version__",
    "SingleInstance",
    "CrashReporter",
    "setup_single_instance_and_crash_reporting",
    "get_instance_id",
]