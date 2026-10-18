"""Stream a Windows batch build to the console and a timestamped local log."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _write_console(data: bytes) -> bool:
    """Write raw child output when possible; return False after a closed pipe."""
    try:
        stream = getattr(sys.stdout, "buffer", None)
        if stream is not None:
            stream.write(data)
            stream.flush()
        else:
            sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: full_build_logger.py FULL_BUILD_BAT [ARGS...]", file=sys.stderr)
        return 2

    batch = Path(args[0]).resolve()
    if not batch.is_file():
        print(f"Build script not found: {batch}", file=sys.stderr)
        return 2

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP")
    if not base:
        print("Neither LOCALAPPDATA nor TEMP is configured", file=sys.stderr)
        return 2

    log_dir = Path(base) / "ZChat" / "build-logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        log_path = log_dir / f"full-build-{stamp}-{os.getpid()}.log"
        child_env = os.environ.copy()
        child_env["ZCHAT_FULL_BUILD_LOG_CHILD"] = "1"
        command = [
            os.environ.get("ComSpec", "cmd.exe"),
            "/d",
            "/c",
            "call",
            str(batch),
            *args[1:],
        ]

        _write_console(f"Full build log: {log_path}\n".encode("utf-8"))
        with log_path.open("xb") as log:
            with subprocess.Popen(
                command,
                cwd=batch.parent,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            ) as child:
                assert child.stdout is not None
                for chunk in iter(lambda: child.stdout.read(4096), b""):
                    log.write(chunk)
                    log.flush()
                    _write_console(chunk)
                return_code = child.wait()
            log.write(f"\r\n[full-build] exit code: {return_code}\r\n".encode("ascii"))
        _write_console(f"\nFull build log saved: {log_path}\n".encode("utf-8"))
        return return_code
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Full build logger failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
