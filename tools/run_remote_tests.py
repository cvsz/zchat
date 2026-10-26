"""Run pytest on the Windows host over SSH (stdlib only).

Tests that need Windows (tkinter GUI, DPAPI, Chrome store, named mutex) skip
on Linux. This script runs them where they pass instead:

  python tools/run_remote_tests.py                          # CloudBot-Manager GUI tests
  python tools/run_remote_tests.py tests/test_update.py -q  # any pytest args

Host/user/repo are supplied by the operator through the environment:
  CAMFROG_WINDOWS_HOST=windows.example  CAMFROG_WINDOWS_USER=USERNAME
  CAMFROG_WINDOWS_DIR=C:/path/to/repository
  CAMFROG_WINDOWS_PYTHON=.venv/Scripts/python.exe
  CAMFROG_SSH_CONTROL_SOCKET=/path/to/ssh-control-socket  (optional)

Authentication reuses your existing SSH setup (keys/agent or an already-open
`ssh -M` control socket). No passwords or tokens are handled here.
"""
import os
import subprocess
import sys

HOST = os.environ.get("CAMFROG_WINDOWS_HOST", "")
USER = os.environ.get("CAMFROG_WINDOWS_USER", "")
REMOTE_DIR = os.environ.get("CAMFROG_WINDOWS_DIR", "")
REMOTE_PY = os.environ.get("CAMFROG_WINDOWS_PYTHON", r".venv\Scripts\python.exe")
CONTROL_SOCK = os.environ.get("CAMFROG_SSH_CONTROL_SOCKET", "")


def build_command(pytest_args):
    """ssh argv that runs pytest with the given args on the Windows host."""
    if not HOST or not USER or not REMOTE_DIR:
        raise ValueError(
            "set CAMFROG_WINDOWS_HOST, CAMFROG_WINDOWS_USER, and CAMFROG_WINDOWS_DIR")
    remote = "cd {0}; {1} -m pytest {2}".format(
        REMOTE_DIR, REMOTE_PY, " ".join(pytest_args))
    cmd = ["ssh"]
    if os.path.exists(CONTROL_SOCK):
        cmd += ["-S", CONTROL_SOCK]
    cmd += ["{0}@{1}".format(USER, HOST), remote]
    return cmd


def main(argv=None):
    pytest_args = list(argv) if argv else ["tests/test_web_status_gui.py", "-v"]
    try:
        cmd = build_command(pytest_args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("+ {0}".format(" ".join(cmd)))
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
