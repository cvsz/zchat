# Account B Linux Chrome Web-Profile Observer

## Purpose

Use a dedicated Chrome session authenticated as **Account B** to inspect the web
profile of **Account A** on `profiles.camfrog.com`. The observer is read-only:
it does not export cookies, automate login, trigger a status change, or claim
that the Camfrog Windows desktop Friend List received a change.

**Important:** No validated status selector for the `/th/zdevz` page is
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

## One-shot observation

On Windows, log into Camfrog as **Account A**, start Status Changer and select
a unique, non-sensitive status marker. On Linux run:

```bash
python tools/web_profile_observer.py \
  --nickname zdevz \
  --expected ZCHAT-E2E-001 \
  --selector 'REPLACE_WITH_INSPECTED_UNIQUE_STATUS_CSS_SELECTOR' \
  --browser-profile "$HOME/.local/share/zchat-observer/account-b" \
  --output "$HOME/zchat-observer-evidence" \
  --login
```

The script opens the allowlisted Camfrog profile, pauses for an interactive
Account B login, then reopens the target page and checks exactly one visible
element against the expected string. Replace the selector **only after**
inspecting the authenticated DOM and confirming it maps to Custom Status.
No selector was invented for the Camfrog web page.

The script records a random `run_id`, UTC observation time, a local viewport
screenshot, and SHA-256 digests. Screenshots can contain personal profile
details: keep evidence local, limit filesystem access and redact before
sharing. JSON evidence does not contain cookie material or the observed text.
Exit 0 means **WEB_PROFILE_MATCH**, not full Windows end-to-end acceptance.
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
