# ZChat Compact GUI Adapter (Windows)

A small C++20/CMake helper for compacting visible ZChat-branded top-level windows.
The initial target is **360 × 480 px**. Resizing uses Win32 window geometry; Windows
UI Automation (UIA) is initialized and queried for each candidate HWND before any
change is attempted. It does not inject into processes, patch binaries, alter
Camfrog/LINE windows, or automate application controls.

## Safety defaults

- Default mode is **dry-run**. It only logs and reports matching windows.
- `--apply` requires at least one `--allow-image` with an existing absolute executable path.
- The title-prefix filter (`ZChat `, `ZChat-`, `ZChat:`) is secondary; a title alone never
  authorizes resizing. Unknown or non-allowlisted process images are skipped.
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

# Apply only to a specific executable image; repeat --allow-image for additional trusted images
.\build\zchat-compact-adapter\Release\zchat-compact-adapter.exe --apply --allow-image "C:\Apps\ZChat\zchat.exe"

# Override dimensions and log path
.\build\zchat-compact-adapter\Release\zchat-compact-adapter.exe --width 420 --height 560 --apply --allow-image "C:\Apps\ZChat\zchat.exe" --log .\adapter.jsonl
```

## Version detection

The adapter reads the owning process image path and PE version. In apply mode, it
canonicalizes and checks the image path against the explicit `--allow-image` list before
calling `SetWindowPos`; failure to read or match the image is fail-closed. The allowlist
identifies the executable image, not a script passed to a shared interpreter. For script
deployments, prefer a dedicated virtual-environment interpreter or a packaged executable
so unrelated Python applications do not share the same allowlist entry.

## Tests and limits

The policy test executable covers title and process-image allowlisting, size bounds, JSON escaping,
and the resize mutation boundary. Injected resize callbacks verify that dry-run, unmanaged titles,
and non-allowlisted process images never invoke the resize operation, while an authorized apply
invokes it exactly once and propagates failures.
Build/runtime validation requires Windows; CI or a Windows developer machine must run
the commands above. A UIA element lookup only establishes that a window is exposed to
UI Automation; it does not establish that all controls fit at 360×480. GUI acceptance
must verify clipping, scrolling, keyboard navigation, and that resize does not change
feature behavior.
