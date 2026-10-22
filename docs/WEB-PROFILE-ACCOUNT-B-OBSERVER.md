# Account B Linux Chrome Web-Profile Observer

## Purpose

Use a dedicated Chrome session authenticated as **Account B** to inspect the web
profile of **Account A** on `profiles.camfrog.com`. The observer is read-only:
it does not export cookies, automate login, trigger a status change, or claim
that the Camfrog Windows desktop Friend List received a change.

**Important:** No validated status selector for the `/th/Seaza` page is
currently part of this repository. The operator must first inspect the page
and establish that a unique visible element genuinely represents the **custom
status**, not profile name, flair, last-seen time, or another account field.
If the web page does not display that status, this integration is **BLOCKED**.

## Linux setup

Install Google Chrome for Linux and an isolated Python environment:

```bash
python3 -m venv .venv-observer
. .venv-observer/bin/activate
python -m pip install --upgrade pip
python -m pip install playwright pytest
python -m playwright install-deps chromium
```

The observer invokes installed Google Chrome via Playwright's `channel="chrome"`
and uses a dedicated persistent Chrome profile. It must not attach to the
operator's normal browsing profile, ingest copied PHPSESSID values, or commit
session cookies.

## Periodic browser-refresh observation

On Windows, log into Camfrog as **Account A**, start Status Changer and select
a unique, non-sensitive status marker. On Linux run:

```bash
python tools/web_profile_observer.py \
  --nickname Seaza \
  --expected ZCHAT-E2E-001 \
  --selector 'REPLACE_WITH_INSPECTED_UNIQUE_STATUS_CSS_SELECTOR' \
  --browser-profile "$HOME/.local/share/zchat-observer/account-b" \
  --output "$HOME/zchat-observer-evidence" \
  --refresh-seconds 30 \
  --samples 20 \
  --login
```

The script opens the allowlisted Camfrog profile, pauses for an interactive
Account B login, then reloads the target page every 30 seconds for 20 observations and checks exactly one visible
element against the expected string. Replace the selector **only after**
inspecting the authenticated DOM and confirming it maps to Custom Status.
No selector was invented for the Camfrog web page.

The script records a random `run_id`, per-refresh UTC observation times, a JSONL sample log, a local viewport screenshot on the first observation and whenever the observed result changes, and SHA-256 digests. Sampling is bounded by `--samples` (1..1000) and `--refresh-seconds` (10..300). Screenshots can contain personal profile
details: keep evidence local, limit filesystem access and redact before
sharing. JSON evidence does not contain cookie material or the observed text.
Exit 0 means that **all samples matched the web-profile element**, not full Windows end-to-end acceptance.
Exit 2 means mismatch, uncertainty, or execution error.

### Verification matrix

| Evidence | Required interpretation |
| --- | --- |
| Browser displays exact expected status in verified field | `WEB_PROFILE_MATCH` |
| Browser displays a different value | `WEB_PROFILE_MISMATCH` |
| Missing, hidden or ambiguous field / login or redirect problem | `UNVERIFIED` |
| Only HTTP 200 / profile title or nickname matches | `UNVERIFIED` |
| Browser match and Windows worker log alone | **Not a proved desktop external effect** |
| Verified Account B native Camfrog Friend List + Windows A action + correlation | Candidate for native E2E acceptance after evidence review |

Before claiming desktop E2E PASS, a reviewer must correlate independently
recorded Windows worker activity and Account B's actual provider-observed
status (where the web surface is proven equivalent to the desktop field)
using a unique run marker and timestamps. Never infer external success from
a queue acknowledgement, cookie presence, page HTTP response or screenshot
without matching the actual field.

## Offline tests

```bash
python -m pytest -q tests/test_web_profile_observer.py
```

Release gate: run GitHub CI, inspect status selector and browser behavior
against the *current* site, perform real two-account acceptance, validate
evidence privacy, and obtain independent review before merge or deployment.

## Source HTML inspection (2026-10-22)

An operator-provided HTML document for `https://profiles.camfrog.com/th/Seaza`
contains `var nick = 'Seaza'`, authenticated navigation markup, and an empty
`<div id="profiles-pages"></div>` Vue mount point. The saved server HTML
**does not contain a Custom Status field or a validated status CSS selector**.
It includes a page-level CSRF token; never commit that token or reuse it as
browser authentication evidence. The displayed header name may identify the
logged-in account, not the inspected subject, so it cannot prove Account B
identity or Account A status.

The page imports dynamic JavaScript and can populate the mount point after
load. The next acceptance step is to inspect a **post-render Chrome DOM** while
logged in as Account B, record a unique selector for the actual Custom Status
field, and verify that it changes after Windows Account A changes its status.
Do not use `.nav-popover-username`, `.nickname-info`, `#profiles-pages`,
`document.body`, or the URL/title as a substitute for that field.
If no status field exists on the page, mark this web-observer path `BLOCKED`
and use a genuine second Camfrog client observer instead.
