import tempfile
from pathlib import Path

from zchat_client.verification.evidence import (
    create_run_dir,
    write_checksums,
    write_manifest,
)


def test_evidence_bundle():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        run_dir = create_run_dir(base, "run-1")
        write_manifest(run_dir, {"run_id": "run-1"})
        (run_dir / "primary" / "worker.jsonl").write_text("{}", encoding="utf-8")
        write_checksums(run_dir)
        assert (run_dir / "checksums.sha256").exists()


def test_reject_unsafe_run_ids(tmp_path):
    import pytest

    for run_id in ("../escaped", "/tmp/escaped", r"..\\escaped", "", "foo/bar"):
        with pytest.raises(ValueError):
            create_run_dir(tmp_path, run_id)
    assert not (tmp_path.parent / "escaped").exists()
