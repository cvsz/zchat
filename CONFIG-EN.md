# config.json reference

Also available in Thai: `CONFIG-TH.md`.

Missing keys use built-in defaults. This reference covers the source CLI configuration; packaged feature apps each create and use a separate config in their own app folder. `config.json` is local and ignored by Git; create it with `python camfrog_auto.py init --config config.json` or copy `config.example.json`. Validate the example with `python camfrog_auto.py check --config config.example.json`.

| Key | Default | Description |
|---|---|---|
| `language` | `auto` | UI/log language: `auto`, `th`, `en` (CLI `--lang` overrides) |
| `reload_config` | `true` | Hot-reload this file when it changes (invalid edits rejected) |
| `stats.enabled/file/write_seconds` | `true` / `stats.json` / `60` | Counters shown by `state` |
| `window_title_regex` | `.*Camfrog.*` | Regex for Camfrog top-level windows. Multiple windows are allowed only when they belong to one process; matches spanning multiple or unverifiable processes fail closed. |
| `dry_run` | `true` | `true` = log only, nothing sent. Random/Marquee apps expose this as **Send statuses live to Camfrog**; unchecked keeps dry-run on. |
| `poll_seconds` | `1.5` | Main loop tick (0.2–60) |
| `log.level/file/max_bytes/backups` | `INFO` / `camfrog_auto.log` | Rotating log. **Background mode needs `file`** |
| `log.log_message_text` | `true` | `false` hides chat/status text in logs |
| `active_hours` | disabled | Act only between `start`–`end` (local, overnight OK). Outside: chat read but never answered |
| `safety.stop_file` | `STOP` | Create this file to stop (cleared on next start) |
| `safety.pid_file` | `camfrog_auto.pid` | PID file for background mode / single instance |
| `safety.require_foreground` | `true` | Refuse to press Enter unless Camfrog is foreground |
| `safety.discovery_mode` | `legacy` | `legacy` finds the Camfrog window by title regex; `verified` resolves it by executable identity, Authenticode signature, PID plus creation time, and structural window role, failing closed on ambiguity |
| `safety.restore_previous_window` | `true` | After sending, return focus to the window you were using. Set `false` to leave Camfrog's chat window in front; IM auto-reply scrolls its local reply echo into the chat viewport either way |
| `safety.max_consecutive_failures` | `5` | Stop after N failed ticks in a row (backoff + re-attach) |
| `status.enabled` | `true` | Rotate status |
| `status.execution_mode` | `interactive` | `interactive` uses real desktop input and may affect focus; it waits for user inactivity (2 s by default, `ZCHAT_USER_IDLE_MS` override, 120 s maximum wait). `strict_background` fails closed with `BACKGROUND_UNSUPPORTED`. `best_effort_background` is a legacy value rejected by live status workers because Camfrog may activate and interrupt keyboard input |
| `status.edit` / `apply_button` | selectors / `null` | UIA selectors from `discover`; `apply_button` is invoked instead of Enter |
| `status.background_enter_target` | `edit` | Legacy submit-target setting retained for compatibility. It does not override the strict-mode capability gate. Do not use it as evidence that a no-interference background status send is supported. |
| `status.interval_seconds` | `600` | Minimum 0.3 |
| `status.retry_seconds` | `30` | Wait after a failed send before retrying (1–3600) |
| `status.set_on_start` | `true` | Apply the first status 10 seconds after the worker starts |
| `status.random` | `false` | Random instead of sequential |
| `status.language_mode` | `both` | For `{th,en}` items: `th`, `en`, `both` (`TH \| EN`), `alternate` |
| `status.schedules[]` | `[]` | `{start, end, messages}`: pool used during that time window (overnight OK); otherwise `status.messages` |
| `status.language_cycle` | `[]` | e.g. `["th","en"]`: alternate languages on every status change (messages or history) |
| `status.marquee.enabled` | `false` | Scroll long statuses frame by frame |
| `status.marquee.scroll` | `false` | `false` = one whole pool line per tick (lines rotate 1..N and wrap); `true` = frame scrolling below |
| `status.marquee.width` / `stride` | `28` / `2` | Window size (8–80, ≤ `max_length`) / Thai-safe clusters advanced per frame |
| `status.marquee.step_seconds` | `0.5` | Seconds between frames (**min 0.3**); the runner wakes at the frame deadline to avoid extra poll delay |
| `status.marquee.cycles` / `max_frames` | `1` / `80` | Finite loops (1–5) and maximum generated ticker frames per status (5–300); finite scrolling then settles on the full text |
| `status.marquee.infinite_loop` | `false` | `true` = repeat the frame list until `status.interval_seconds` expires, then advance to the next status; `cycles` ignored |
| `status.marquee.separator` | `   •   ` | Gap shown between end and restart of the ticker |
| `status.history.enabled` / `file` | `true` / `status_history.json` | Save every status you set |
| `status.history.use_as_source` | `false` | `true` = next status is picked from history (`status.schedules` are then ignored) |
| `status.history.mode` | `rotate` | `rotate` (round-robin, least recently used first), `random`, `most_used` |
| `status.history.seed_from_messages` | `true` | Copy `status.messages` (both languages) into history at start |
| `status.history.record` / `max_items` | `true` / `50` | Record what is sent / keep at most N (least used dropped first) |
| `status.max_length` | `120` | Truncate status |
| `status.messages` | `[]` | Strings or `{ "th": "...", "en": "..." }` objects |
| `autoreply.enabled` | `true` | Auto reply |
| `autoreply.window_title_regex` | `""` | Extra filter on the room window title (empty = no extra filter) |
| `autoreply.log_kind` | `room` | Which log the room bot reads: `room`, `im`, or `any` |
| `autoreply.history_tail` | `150` | Chat lines read per poll for reply decisions |
| `autoreply.own_nickname` | `""` | Your nick (never answered). **Required for live mode** |
| `autoreply.history` / `input` | selectors | Chat history control / chat input control |
| `autoreply.ignore_nicknames` / `only_nicknames` | `[]` | Skip these nicks / answer only these (empty = everyone) |
| `autoreply.per_sender_cooldown_seconds` | `300` | Min gap per person |
| `autoreply.global_min_gap_seconds` | `20` | Min gap between any two replies |
| `autoreply.max_per_hour` | `30` | Hard hourly cap |
| `autoreply.delay_range_seconds` | `[2,5]` | Random delay before replying |
| `autoreply.max_reply_length` / `max_incoming_length` | `200` / `500` | Output cap / input length for regex |
| `autoreply.skip_patterns` | `[]` | Regexes; matching messages are never answered |
| `autoreply.ignore_links` | `true` | Never answer messages containing `http://`, `https://`, `www.` |
| `autoreply.rules[]` | `[]` | `pattern` (regex, case-insens.), `reply`, optional `cooldown_seconds`, `enabled`, `lang` (`th`/`en`: match only messages in that language), `mention` (`true`: only when `own_nickname` appears in the message). First matching rule wins, so put specific rules first |

## Private-message (IM) auto-reply

Off by default and separate from room auto-reply: its own rules, allowlist and limits. It reuses `autoreply.history` / `autoreply.input` / `autoreply.own_nickname`. Only windows whose log pane is `wb-log-im-data` are touched; room windows and the unverified `wb-log-mtim-data` kind are never answered. In the source CLI, `python camfrog_auto.py im-probe --config config.json` lists config problems and classifies Camfrog windows. Test offline with `python camfrog_auto.py test-rules "text" --sender Friend --im --config config.json`.

| Key | Default | Description |
|---|---|---|
| `autoreply_im.enabled` | `false` | Opt in to IM auto-reply |
| `autoreply_im.dry_run` | `true` | IM has its own dry-run. The global `dry_run: true` always wins |
| `autoreply_im.only_nicknames` | `[]` | **Required, non-empty.** Only these friends are ever answered (case-insensitive) |
| `autoreply_im.rules[]` | `[]` | Same format as `autoreply.rules`, but IM-only: room rules never answer a private chat |
| `autoreply_im.skip_patterns` / `ignore_links` | `[]` / `true` | Never answer matching messages / messages with links |
| `autoreply_im.prefix` | `"[auto] "` | Put in front of every reply so friends know it is automatic. Incoming lines that start with it are never answered (no bot-to-bot ping-pong). Must not start with `/` |
| `autoreply_im.answer_first_message` | `true` | A window that opens while the bot runs with exactly one line, from a listed friend, is answered. Windows already open at start, and windows with several lines (reloaded history), are baselined silently |
| `autoreply_im.per_sender_cooldown_seconds` | `3600` | Min gap per friend (min 60) |
| `autoreply_im.max_per_sender_per_day` | `3` | Rolling 24 h cap per friend (1–50) |
| `autoreply_im.max_per_hour` / `global_min_gap_seconds` | `10` / `20` | Hourly cap (1–100) and min gap between any two IM replies, across all windows |
| `autoreply_im.delay_range_seconds` | `[2,5]` | Random delay before replying |
| `autoreply_im.max_reply_length` / `max_incoming_length` | `200` / `500` | Reply cap (the prefix counts and is never cut) / input length for regex |
| `autoreply_im.log_kinds` | `["im"]` | Window kinds treated as private chat. `im` is the verified one; add `mtim` only if `im-probe` shows your private chats are `MTIM` windows (unverified) |
| `autoreply_im.max_windows` | `5` | Most private-chat windows tracked at once (1–20) |
| `autoreply_im.window_title_regex` | `""` | Optional filter on the IM window title |

Validation (errors, not warnings, when `enabled`): empty `only_nicknames`; empty `autoreply.own_nickname` (the bot would answer itself; also refused in dry-run); `autoreply.log_kind` other than `room` while room auto-reply is on; bad regexes; out-of-range limits. Incoming message text is never written to the log; only the friend's nick and your own canned reply are.

## Music DJ (room)

Needs `autoreply.enabled` for chat plumbing. Chat commands (prefix from `dj.prefix`, default `!`): `!request <song>` (aliases `!req`, `!song`), `!queue`, `!current` (alias `!np`), `!skip`, `!help`. The queue lives in `dj.queue_file` beside the app.

| Key | Default | Meaning |
|---|---|---|
| `dj.enabled` | `false` | Turn on the DJ |
| `dj.prefix` | `"!"` | Command prefix; must not start with `/` |
| `dj.queue_file` | `"dj_queue.json"` | Persistent `{current, queue[]}` state file |
| `dj.max_per_user` | `3` | Songs queued per person (1–50), case-insensitive per nick |
| `dj.music_dir` | `"music"` | Folder with `.wav` files (for `local` backend and `!request` matching) |
| `dj.audio_backend` | `"chat"` | `chat` = announce only; `local` = also play the `.wav` on this PC (route it into Camfrog with a virtual cable or stereo mix; MP3 must be converted to WAV first) |
| `dj.announce_now` | `"[dj] Now playing: {title} (requested by {user})"` | Template; placeholders `{title}` `{user}` |
| `dj.announce_queued` | `"[dj] Queued #{pos}: {title}"` | Template; placeholders `{pos}` `{title}` |

Rules: the first request starts playing immediately; duplicates and the currently playing song are refused (case-insensitive); `!skip` works for the requester or the bot owner (your `autoreply.own_nickname`); with `local` playback the next song starts automatically when the current file's duration ends, otherwise start the next song yourself. Lines starting with the DJ prefix are never matched by room auto-reply rules.

## `reply` formats

- `"text"` — one reply
- `["a","b"]` — random pick
- `{ "th": [...], "en": [...] }` — language follows the incoming message (any Thai character = `th`, else `en`); falls back to the other language if one is missing

Variables in statuses and replies: `{sender}` (replies only), `{me}` (your nick), `{time}` (HH:MM), `{date}` (YYYY-MM-DD). Replies starting with `/` are always blocked, so rules can never emit room commands.

## Validation notes

- Selectors with no criteria (none of `control_type`, `auto_id`, `class_name`, `title`, `title_re`) are errors. Identical selectors for `status.edit`, `autoreply.history`, `autoreply.input` are a warning in dry-run and an **error when `dry_run` is false** (one control would be driven for two jobs).
- Ranges: `poll_seconds` 0.2–60, `safety.max_consecutive_failures` 1–100, `status.max_length` 1–500, `status.retry_seconds` 1–3600, `autoreply.max_reply_length` 1–500, `autoreply.max_incoming_length` 1–2000.
- More than 25 new chat lines in one poll (room switch / history reload) are treated as a reset and never answered.
- In regexes do not wrap Thai words in `\b` (Thai has no word boundaries): use `เว็บพนัน`, not `\bเว็บพนัน\b`.
