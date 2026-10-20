import os
import tempfile
from pathlib import Path

from zchat_client.verification.config import load_config
from zchat_client.verification.evidence import create_run_dir


def test_boolean_parsing_rejects_unknown_spellings():
    for bad in ("maybe", "2", "onion", "", "yes please"):
        try:
            load_config({"ZCHAT_VERIFICATION_ENABLED": bad})
            assert False, f"accepted {bad!r}"
        except ValueError:
            pass


def test_run_id_path_containment_property():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        for bad in ("../x", "a/b", "a\\b", "", ".", "..", "a\x00b"):
            try:
                create_run_dir(base, bad)
                assert False, f"accepted {bad!r}"
            except ValueError:
                pass
        # valid
        rd = create_run_dir(base, "run-1")
        assert rd.resolve().is_relative_to(base.resolve())
        assert (base / "run-1").exists()
