"""Stream a Windows batch build to the console and a timestamped local log."""

from __future__ import annotations

import datetime as dt
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

DEFAULT_ESTIMATE_SECONDS = 10 * 60
_LOG_NAME = re.compile(r"^full-build-(\d{8})-(\d{6})-(\d+)-\d+\.log$")


def _estimate_duration_seconds(log_dir: Path) -> int:
    """Estimate duration from recent successful builds, with a 10-minute fallback."""
    durations: list[tuple[float, int]] = []
    for path in log_dir.glob("full-build-*.log"):
        match = _LOG_NAME.match(path.name)
        if match is None:
            continue
        try:
            start = time.mktime(
                time.strptime(f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S")
            )
            with path.open("rb") as log:
                log.seek(max(0, path.stat().st_size - 4096))
                tail = log.read()
            if b"FULL BUILD COMPLETE" not in tail:
                continue
            duration = int(path.stat().st_mtime - start)
        except (OSError, ValueError, OverflowError):
            continue
        if 30 <= duration <= 4 * 60 * 60:
            durations.append((start, duration))
    if not durations:
        return DEFAULT_ESTIMATE_SECONDS
    recent = sorted(durations)[-5:]
    return int(statistics.median(duration for _started, duration in recent))


def _format_estimate(seconds: int) -> str:
    minutes, remainder = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remainder:02d}"


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
        stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        log_path = log_dir / f"full-build-{stamp}-{os.getpid()}.log"
        estimated_seconds = _estimate_duration_seconds(log_dir)
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

        _write_console(f"Full build log: {log_path}\n".encode())
        _write_console(
            (
                "Estimated build duration: "
                f"{_format_estimate(estimated_seconds)} (approximate, based on prior successful builds).\n"
            ).encode()
        )
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
                output_queue: queue.Queue[bytes | None] = queue.Queue()

                def read_child_output() -> None:
                    for output_chunk in iter(lambda: child.stdout.read(4096), b""):
                        output_queue.put(output_chunk)
                    output_queue.put(None)

                reader = threading.Thread(target=read_child_output, daemon=True)
                reader.start()
                started = time.monotonic()
                next_tick = started + 30
                while True:
                    try:
                        chunk = output_queue.get(timeout=0.2)
                    except queue.Empty:
                        chunk = b""
                    now = time.monotonic()
                    if now >= next_tick:
                        elapsed = int(now - started)
                        remaining = estimated_seconds - elapsed
                        if remaining >= 0:
                            status = (
                                "\n[build timer] Approximate time remaining: "
                                f"{_format_estimate(remaining)} "
                                f"(elapsed {_format_estimate(elapsed)}).\n"
                            )
                        else:
                            status = (
                                "\n[build timer] Estimate exceeded by: "
                                f"{_format_estimate(-remaining)} "
                                f"(elapsed {_format_estimate(elapsed)}).\n"
                            )
                        _write_console(status.encode("ascii"))
                        next_tick = now + 30
                    if chunk is None:
                        break
                    if not chunk:
                        continue
                    log.write(chunk)
                    log.flush()
                    _write_console(chunk)
                reader.join()
                return_code = child.wait()
            log.write(f"\r\n[full-build] exit code: {return_code}\r\n".encode("ascii"))
        _write_console(f"\nFull build log saved: {log_path}\n".encode())
        return return_code
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Full build logger failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
