"""Write the SHA-256 manifest for a staged LINE executable."""

from __future__ import annotations

import hashlib
import argparse
import os
import tempfile
from pathlib import Path


def write_build_manifest(stage_dir: str | os.PathLike[str]) -> Path:
    """Hash the staged executable and atomically replace its manifest."""
    stage = Path(stage_dir)
    executable = stage / "line-status-changer.exe"
    if executable.is_symlink() or not executable.is_file():
        raise FileNotFoundError(f"Staged executable is missing or not a regular file: {executable}")

    digest = hashlib.sha256()
    with executable.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)

    manifest = stage / "SHA256SUMS.txt"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            newline="\n",
            dir=stage,
            prefix=".SHA256SUMS.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(f"{digest.hexdigest()}  {executable.name}\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, manifest)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage_dir", type=Path)
    arguments = parser.parse_args()
    print(write_build_manifest(arguments.stage_dir))
