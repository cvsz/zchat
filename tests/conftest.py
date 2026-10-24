"""Shared Tk root for every GUI smoke test.

Only one Tcl interpreter should be created per test process: some Windows
Python installs fail to initialize Tcl a second time after a root is destroyed.
Smoke tests therefore reuse this single root and never destroy it.

Other GUI tests (e.g. `test_private_chat_gui`) create and destroy their own
roots, which clears tkinter's default-root pointer. `tk_root()` detects that and
replaces the cached root so every smoke test always gets a usable, *default*
root — `tk.BooleanVar()` and friends fail with "no default root window"
otherwise.
"""
import pytest

_ROOT = None


def _usable(root):
    import tkinter
    try:
        return root.winfo_exists() and tkinter._default_root is root
    except Exception:
        return False


def tk_root():
    global _ROOT
    import tkinter
    if _ROOT is not None and not _usable(_ROOT):
        try:
            _ROOT.destroy()
        except Exception:
            pass
        _ROOT = None
    if _ROOT is None:
        _ROOT = tkinter.Tk()
        _ROOT.withdraw()
    return _ROOT


def has_display():
    try:
        tk_root()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def shared_tk_root():
    return tk_root()
