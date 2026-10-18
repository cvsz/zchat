# ZChat Control Server: Architecture and Evidence Boundaries

ZChat Control Server keeps Camfrog-specific behavior behind its provider integration. This document records the current implementation against the supplied browser and source observations. Research summaries are not substitutes for source artifacts or sanitized runtime captures. The implementation keeps these evidence levels separate.

## State separation

`Agent.runtime_state` describes only the polling agent process (`unknown`, `starting`, `running`, `error`, or `disconnected`). It does not mean a Camfrog account is logged in, online, in a room, or that a Cloud Bot is running.

`AccountRuntimeSnapshot.process_state` is account-scoped and reported by the agent. The current Windows adapter can report only the specific process handle it launched; it cannot discover pre-existing Camfrog processes. Reports older than 30 seconds are returned as `unknown`. `room_state` stays `unknown` until a verified live room-status source is available; accepting a deep link does not establish room membership.

The account auth-state detector uses the observed `.nav-user-logged` marker and `external_data.type` values `profile`/`settings`; `public` is kept separate. A CSRF field is reduced to a boolean-presence observation. The server never receives its value. Profile, friends, auth, and human presence are stored as separate snapshots. Public image-based presence remains `UNKNOWN` until it is calibrated against controlled account states, and stale presence becomes `UNKNOWN` after ten minutes.

## Report findings and implementation status

| Report finding | Evidence limit | Current treatment |
|---|---|---|
| Login selectors and authenticated-state marker | UI trace confirms selectors; the added login drill confirms `.nav-user-logged` plus `external_data.type`; login POST fields and redirect chain remain unknown | Bootstrap admin uses a short-lived dashboard JWT. Agent snapshots store only derived auth state, page type, and CSRF-presence boolean; no Camfrog login API or session storage |
| Profile and friend AJAX endpoint inventory | Source confirms `/ajax/get_friend_list.php`, `/ajax/get_recently_added_list.php`, and other profile endpoints; uploaded HARs still do not contain these runtime calls | Allowlisted `external_data` projection, friend-entry normalization, and snapshot APIs exist. No authenticated fetcher is wired |
| Human presence strings | Structured `online`, `away`, `busy`, and `offline` are known; public image semantics are uncalibrated | Snapshot APIs expose normalized state. HTTP 200 or status-image presence never implies ONLINE |
| Store purchase handler and CSRF field | Frontend source maps the request shape; no captured runtime response validates the server envelope | Operator-imported catalog and consent-only purchase intents exist. There is no checkout or purchase executor |
| Cloud Bot RPC helper and visible state strings | Client source describes request/poll behavior; a real `/bot/ajax/` response is still required | Request/response projection, status mapping, registry, and snapshots exist. Live authenticated transport is not configured |
| Official Camfrog URI patterns | URI strings were observed; Windows runtime behavior has not passed acceptance | Opt-in provider launches/stops only the process it created and dispatches official room/add-friend/IM URIs. It does not prove Camfrog completed the action |
| Gold/premium nickname settings UI | Exact request, eligibility, timing, and price response remain unvalidated | Subscription baseline and temporary overlays are modeled. No nickname-change command exists |

The captured HARs documented by the reports were dominated by third-party traffic and did not validate Camfrog login, profile AJAX, Store purchase, or Cloud Bot runtime contracts. Do not treat source-level endpoint discovery as a successful live integration.

## Control-plane slice

```text
Same-origin operator dashboard
        │ bootstrap token → short-lived JWT
        ▼
FastAPI control plane ─── PostgreSQL
        ▲                    │
        │ scoped agent token │ account / command / chat / audit state
        └── Windows agent ───┘
                 │
                 └── optional official client process/URI provider
```

An agent token is rotated and bound to the current account assignment; reassignment invalidates the previous and replacement agents' prior tokens. An administrator can rotate an active token without changing its assignment; the replacement is returned once. The database stores account nicknames and internal assignments, plus plaintext internal chat bodies. It does not store login passwords, browser profiles, cookies, CSRF values, CAPTCHA answers, payment credentials, or room access keys. Auth-state snapshots contain only a derived state, page type, and whether a CSRF field was present. Agent and command claim tokens are returned once and stored as hashes; claim tokens are single-use and bounded by a lease. Chat-body retention, backup access, and deletion policy remain deployment gates.

The API rate limiter is bounded and per process. It is suitable for local/single-instance development only; it does not coordinate across multiple workers or replicas. Structured request logs include only method, path, status, request id, and duration. Query strings, headers, and bodies are omitted.

Expired command claims become terminal `effect_unknown` outcomes and are not automatically retried, because the provider may have acted before losing connectivity. Additional commands are blocked while an account has a claimed or unknown command, preventing overlapping dispatch and preserving the account after uncertain effects. An administrator can record a manual reconciliation with an external evidence SHA-256; the resulting `operator_reconciled` state is an attestation, not provider verification. The shared JWT principal is `operator`, so audit records do not identify an individual human. The Windows URI provider reports `effect_unknown` after the OS accepts a URI; this is handoff evidence only. Internal chat messages use a separate at-least-once protocol.

## Current implementation and remaining gates

1. **Control API and dashboard:** core account/agent/command/audit routes, health, JWT sessions, in-process rate limiting, and operator views are implemented.
2. **Windows agent:** launch/stop/restart of an agent-owned process and official URI dispatch are implemented behind an opt-in provider. Account runtime heartbeats report process state, while room state remains unknown. Windows GUI/runtime/session isolation still needs acceptance.
3. **Profile/friends/presence projection:** allowlisted data projection, friend normalization, snapshot ingestion, and stale-aware presence reads are implemented. Live authenticated fetching and status updates remain disabled pending an authorized local browser provider and request/response validation.
4. **Cloud Bot:** status mapping, request construction, response projection, and snapshots are implemented. Authenticated transport and action routes remain disabled until a real runtime envelope is captured.
5. **Store and premium account actions:** operator-imported catalog and consent-only purchase intents exist. No checkout, purchase, nickname change, or security mutation is executed.
6. **Production gates:** exact login network contract, Windows/VM isolation, runtime captures, shared rate limiting, PostgreSQL deployment, backup/restore, secret rotation, and soak evidence remain open.
