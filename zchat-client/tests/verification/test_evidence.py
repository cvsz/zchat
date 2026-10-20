import tempfile
from pathlib import Path

from zchat_client.verification.evidence import create_run_dir, write_checksums, write_manifest


def test_evidence_bundle():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        run_dir = create_run_dir(base, "run-1")
        write_manifest(run_dir, {"run_id": "run-1"})
        (run_dir / "primary" / "worker.jsonl").write_text("{}", encoding="utf-8")
        write_checksums(run_dir)
        assert (run_dir / "checksums.sha256").exists()
