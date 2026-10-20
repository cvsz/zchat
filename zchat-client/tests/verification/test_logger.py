import json
import tempfile
from pathlib import Path

from zchat_client.verification.logger import VerificationLogger


def test_logger_redacts():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.jsonl"
        logger = VerificationLogger(p, redact_pii=True)
        rec = logger.log("observer_started", password="secret", token="abc")
        assert rec["password"] == "[REDACTED]"
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["event"] == "observer_started"


def test_logger_redacts_observer_pii(tmp_path):
    logger = VerificationLogger(tmp_path / "evidence.jsonl")
    record = logger.log("observation", target_account="person", expected_status="private status", display_name="nickname")
    for key in ("target_account", "expected_status", "display_name"):
        assert record[key] == "[REDACTED]"
