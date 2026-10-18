# Camfrog status submit modes

Status Changer, Random Status, and Marquee Status expose two execution modes.
The default remains fail-closed.

| Mode | Behavior | Evidence and limitation |
|---|---|---|
| `strict_background` | Uses the strict submit engine and refuses while its safety gate is closed. | Current Camfrog 8.5 evidence does not establish a non-activating submit route. |
| `best_effort_background` | Writes the verified status Edit and posts Enter to the `CComboBoxTS` combo (`combo-post`). | It avoids the observed Edit Enter ding, but Camfrog may briefly activate or flash. The app does not inject global input and logs each attempt as `STATUS_SUBMIT=DISPATCHED_UNVERIFIED`. |

Choose `best_effort_background` in the feature's Submit mode control. The saved
mode is used by its worker. To stop a worker, use its Stop control. Check the
sanitized worker log and verify the resulting status through another Camfrog
account's friend list. Local control readback is not provider verification.

### Worker startup handshake

Hidden status workers write their PID and wait for the launching GUI to verify
it before entering the status loop. If registration is not verified, the
parent does not authorize status work and the child exits after its bounded
startup wait. This prevents a worker from submitting while the GUI reports a
startup failure. It does not make `best_effort_background` non-activating or
prove delivery to Camfrog's servers.

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

The `STATUS_SUBMIT=DISPATCHED_UNVERIFIED` warning records route and outcome
state only. Other worker log entries can include status text when
`log_message_text` is enabled (the default); disable that setting to keep
status text out of worker logs.
`STATUS_SUBMIT=DISPATCHED_UNVERIFIED` means the message was dispatched; it is
not a success result. Provider verification requires checking the account from
another client or device. The desktop launcher's JSON also marks login and
provider status as unverified.
