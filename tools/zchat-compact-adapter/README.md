# ZChat Compact GUI Adapter (Windows)

A small C++20/CMake helper for compacting visible ZChat-branded top-level windows.
The initial target is **360 × 480 px**. Resizing uses Win32 window geometry; Windows
UI Automation (UIA) is initialized and queried for each candidate HWND before any
change is attempted. It does not inject into processes, patch binaries, alter
Camfrog/LINE windows, or automate application controls.

## Safety defaults

- Default mode is **dry-run**. It only logs and reports matching windows.
- Use `--apply` to resize matching visible, non-minimized windows.
- The allowlist is title-prefix based (`ZChat `, `ZChat-`, `ZChat:`); unrelated
  windows are excluded.
- Size validation is bounded to 320×240 through 4096×4096.
- Existing window position, z-order, and foreground focus are preserved.
- JSON Lines diagnostics are appended to `zchat-compact-adapter.jsonl` by default.

## Build

Use Visual Studio 2022/2026 Build Tools with the Desktop development with C++
workload and CMake 3.20+:

```powershell
cmake -S tools/zchat-compact-adapter -B build/zchat-compact-adapter -A x64
cmake --build build/zchat-compact-adapter --config Release
ctest --test-dir build/zchat-compact-adapter -C Release --output-on-failure
```

## Run

```powershell
# Inspect candidates without changing anything
.\build\zchat-compact-adapter\Release\zchat-compact-adapter.exe

# Apply compact size to visible ZChat windows
.\build\zchat-compact-adapter\Release\zchat-compact-adapter.exe --apply

# Override dimensions and log path
.\build\zchat-compact-adapter\Release\zchat-compact-adapter.exe --width 420 --height 560 --apply --log .\adapter.jsonl
```

## Version detection

The adapter includes a Windows file-version reader based on the PE version resource.
The first release logs adapter version and per-window outcomes. It deliberately does
not infer a Camfrog client version from the window title or modify the client. Process
image path/version reporting is a follow-up before using version-specific resize rules.

## Tests and limits

The policy test executable covers title allowlisting, size bounds, and JSON escaping.
Build/runtime validation requires Windows; CI or a Windows developer machine must run
the commands above. A UIA element lookup only establishes that a window is exposed to
UI Automation; it does not establish that all controls fit at 360×480. GUI acceptance
must verify clipping, scrolling, keyboard navigation, and that resize does not change
feature behavior.
