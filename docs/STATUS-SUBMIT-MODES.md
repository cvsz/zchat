# Camfrog status submit modes

Status Changer, Random Status, and Marquee Status expose two execution modes.
The default remains fail-closed.

| Mode | Behavior | Evidence and limitation |
|---|---|---|
| `strict_background` | Uses the strict submit engine and refuses while its safety gate is closed. | Current Camfrog 8.5 evidence does not establish a non-activating submit route. |
| `best_effort_background` | Disabled for live status workers and status commits. | Windows testing confirmed that Camfrog can take desktop focus during `combo-post`, interrupting keyboard input. Existing configurations are rejected before a worker starts or status text is written. |

Use `strict_background` for live workers. It currently refuses before writing
because no submit route has been proven not to activate Camfrog. Stop any
already-running older worker with the app's Stop control; this guard applies
after rebuilding and restarting the app. Do not use the previous
`best_effort_background` route on the normal desktop. Provider delivery remains
unverified.

Interactive submission can focus Camfrog, click controls, or send real
keystrokes. Before these input-injecting paths, the app waits for 2 seconds of
user inactivity (`ZCHAT_USER_IDLE_MS` overrides the delay; `0` disables the
wait). If input continues for 120 seconds, that attempt is deferred. This
reduces collisions with active use; it does not guarantee that Camfrog will not
activate or flash. The previous `best_effort_background` route used targeted
window messages, but tests confirmed that Camfrog may still take focus. It is
disabled for live status operations; targeted messages are not treated as
desktop isolation.

### Worker startup handshake

Hidden status workers write their PID and wait for the launching GUI to verify
it before entering the status loop. If registration is not verified, the
parent does not authorize status work and the child exits after its bounded
startup wait. This prevents a worker from submitting while the GUI reports a
startup failure. The launcher creates the app's data directory before touching
PID or gate files, including on first run. Standalone status apps save loaded
settings and attempt control discovery on open. Live status operations remain
fail-closed until a non-activating route is verified; worker startup discovery
does not prove delivery to Camfrog's servers.

## Separate desktop launcher

`tools/launch_camfrog_isolated_desktop.py` is a Windows-only manual test tool.
It creates (or opens) a named desktop and launches the installed Camfrog
executable with `STARTUPINFO.lpDesktop` set to that desktop. It refuses to
launch if it sees another Camfrog process, to avoid single-instance forwarding
to a client on the visible desktop.

```powershell
python .\tools\launch_camfrog_isolated_desktop.py launch `
  --exe "C:\Users\USER\AppData\Local\Programs\Camfrog Video Chat\Camfrog Video Chat.exe"

python .\tools\launch_camfrog_isolated_desktop.py inspect

python .\tools\launch_camfrog_isolated_desktop.py switch-test --seconds 180
```

`launch` does not switch desktops, sign in, send a status, or terminate the
client. A successful `launch` only proves that `CreateDesktopW` and
`CreateProcessW` returned successfully. `inspect` can confirm that Camfrog
windows exist on that desktop; it cannot establish successful login, normal
client operation, status delivery, or that another account sees the change.
`switch-test` switches to the named desktop for 5–1800 seconds and then tries
to return to `Default`, so use it when you are ready to view and manually test
Camfrog there. The console will also be on the test desktop during the timer.
It does not attach the ZChat status workers to that desktop. Login and provider
delivery checks require a real Windows session and a second Camfrog account.

A separate Win32 desktop isolates window activation from the visible desktop
while it remains inactive. It is still in the same Windows session; it does not
isolate system audio, and switching to it makes it the interactive desktop.
Do not treat it as a guarantee against every desktop or session side effect.
If Camfrog or UI Automation cannot operate on it, report that limitation rather
than falling back to global keyboard or mouse input.

## Logging

Older builds may contain `STATUS_SUBMIT=DISPATCHED_UNVERIFIED`; it means only
that a message was dispatched, not that Camfrog accepted or published it. The
current build refuses that route. Other worker log entries can include status
text when `log_message_text` is enabled (the default); disable it to keep status
text out of worker logs. Provider verification requires checking the account
from another client or device. The desktop launcher's JSON also marks login and
provider status as unverified.
