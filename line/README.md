# LINE Status Changer

A separate Windows utility in this `line/` folder. Its source, build environment, executable, logs, and `line_config.json` are specific to this utility. It does not read or write Camfrog's `config.json` or require Camfrog's Python environment.

## Build

Run `build.bat` from this folder in a normal, non-elevated PowerShell window. It creates a private `.venv` with the pinned packages in `requirements-build.txt`, runs the focused unit tests and syntax compilation, builds into a staging folder, validates the config, then publishes `dist\line-status-changer.exe` with `dist\SHA256SUMS.txt` and `dist\line_config.json`. The publisher verifies the manifest against the executable before and after publication. It carries forward the live config from `dist` and refreshes it after stopping the old app, so recent drafts are retained. The build process only ends a running `line-status-changer.exe` whose executable path is inside this folder's `dist` directory. Finish any in-progress LINE status update before building. It preserves the last good `dist` if compilation or packaging fails. Root `full-build.bat` invokes this build after the Camfrog apps; CI distributes the executable and its manifest, never `line_config.json`.

Python 3.12 and network access for the first dependency install are required. Later builds reuse this folder's `.venv`.

## Set a profile status

1. Open LINE for Windows and go to **Settings > Profile**.
2. Click the pencil beside **Status message**, then click the status text box.
3. Return to this app and choose or enter a message. Click **Set in LINE** and confirm the preview.
4. The app changes the field only if Windows UI Automation confirms the focused control is the LINE **Status message** editor and can identify one Save button in that same window. If it cannot prove both, it makes no change; use **Copy** or **Paste** and edit/save the field manually.

LINE's status-message instructions are in the [LINE Help Center](https://help.line.me/line/?contentId=20000134).

Setting the status uses Windows UI Automation and leaves the clipboard alone; the explicit **Copy** and **Paste** actions use the Windows Unicode clipboard. After invoking Save, verify the resulting status on your LINE profile. The local input guard allows one-line text up to 5,000 Unicode characters; LINE may enforce a shorter limit.

## Config, tray, and diagnostics

- Edit `dist\line_config.json` to update the saved-message list while the app is closed. It stores the last draft and saved messages, with Unicode support and atomic file replacement.
- **Use**, **Random**, **Add current**, **Remove**, **Copy**, and **Paste** manage the message list and draft. Text fields also support Ctrl+C/X/V/A, Ctrl+Insert, Shift+Insert, and right-click clipboard menus.
- Press **X** to hide the app in the Windows system tray. Click the tray icon to reopen it; right-click for **Show** or **Exit**. If Windows cannot create the tray icon, the app reports that and minimizes to the taskbar.
- Only one app instance can run at a time, preventing competing config writes.
- Rotating logs are stored in `%LOCALAPPDATA%\LINE Status Changer\logs\app.log`.

## Included tools and limits

- **Status messages:** edit a draft, save up to 1,000 reusable messages, choose one at random, copy it, or restore a recent entry from the 500-item history.
- **Schedules:** weekly weekday/time rules and minute intervals, with pause/resume. The app must remain running (it can stay in the tray). At the scheduled time, it only updates LINE when the selected LINE window is foreground and UI Automation verifies the Status message editor and its Save button; otherwise that run is skipped.
- **Presets:** save up to 25 named messages.
- **Windows options:** start with Windows and use Ctrl+Alt+L to show the app. Both are optional and can be disabled in Tools & Settings.
- **Profile helpers:** read the current status, set a display name after confirming the profile-name editor, and choose a profile or cover image in LINE's already-open native file picker. LINE still handles image cropping and final save.

These helpers use LINE's visible Windows interface. They do not bypass LINE authentication or update a profile while LINE is closed. If the current LINE version does not expose a control clearly through Windows UI Automation, the app stops without writing and the status can be copied for a manual update.

Run the unit checks manually from this folder with `python -m unittest discover -s tests -v`.
After a build, an interactive Windows session can also run `.venv\Scripts\python.exe tools\smoke_line_x.py`. It launches with a temporary config, physically clicks the title-bar X, confirms the window is hidden while the tray process remains alive, sends the tray Exit command, checks clean process exit, and verifies that the temporary config did not change the Windows startup entry.
