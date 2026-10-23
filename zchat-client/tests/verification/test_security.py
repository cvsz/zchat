import tempfile
from pathlib import Path

from zchat_client.verification.evidence import create_run_dir
from zchat_client.verification.logger import VerificationLogger


def test_redaction():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.jsonl"
        logger = VerificationLogger(p, redact_pii=True)
        rec = logger.log("test", password="secret", token="abc", safe="ok")
        assert rec["password"] == "[REDACTED]"
        assert rec["token"] == "[REDACTED]"
        assert rec["safe"] == "ok"


def test_path_traversal_safe():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        run_dir = create_run_dir(base, "run-1")
        assert run_dir.resolve().is_relative_to(base.resolve())
