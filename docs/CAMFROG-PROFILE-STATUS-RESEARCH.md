# Camfrog Profile `What's hopping` status research

Initial research on 2026-10-18 used first-party Camfrog pages, anonymous HTTPS GETs, and the existing ZChat source/docs. A separate operator-authorized password sign-in attempt was rejected with CAPTCHA; that attempt established no authenticated profile session and used no session cookie. No status POST or status change was made.

Follow-up on 2026-10-18: the operator supplied local development cookies through the ignored `.env` keys `WEBAUTH_USER` (`PHPSESSID`) and `WEBAUTH_BOT_USER` (`cloudbot`). One authenticated GET to `profiles.camfrog.com/home.php` returned the signed-in profile markers, and one GET to `www.camfrog.com/bot/profile` returned the expected Bot Manager profile form. Cookie values and provider identity values were not recorded. No status POST, Bot Manager form submission, or other mutation was made. This verifies the current local cookies for read-only access only; it does not verify status write behavior or public status visibility.

Additional read-only acceptance preflight on 2026-10-18 matched the newly supplied recipient profile session to its expected account. The newly supplied sender-specific session redirected to the login page; the existing profile session still matched the operator-designated sender account. `WEBAUTH_BOT_USER` is a Bot Manager cookie, not a profile session. Windows showed one Camfrog 8.5.0.51219 main client process. The desktop process identity could not be bound to the profile session from the available UI evidence, and the authenticated profile page contained no contact-list view. No status was changed. Cross-account friend-list visibility is therefore still unverified; do not treat the local candidate build or profile-session GET as live acceptance.

A fresh read-only request to the Bot Manager profile using its configured cookie redirected to the sign-on page. The current Bot Manager session is therefore not authenticated for a live identity check. No Bot Manager action was submitted.

Additional read-only inspection on 2026-10-18 found one `input#hopping` in the authenticated profile HTML, with no server-rendered `value` attribute. The page's first-party URL literals include `/ajax/update_status.php`, but no status or hopping read endpoint. This narrows the available baseline evidence: the page currently provides a write request path but no observed way to recover the pre-change text for safe restore or to verify the result by profile read-back. This does not rule out a getter outside the inspected page or a provider-side view not loaded by that page.

## Findings

### What the status field means

Camfrog's [Video Chat User Guide](https://www.camfrog.com/en/camfrogvideochatguide.phtml) says a user can set a custom status in the `What's hopping` field and that other users see it next to the nickname in their contact lists. This is the custom text field; it is distinct from presence choices such as Online, Away, Busy, Invisible, and Privacy. The guide describes the desktop UI and profile page, but publishes no status-write API contract.

### Anonymous page checks

These read-only GET requests sent no cookies:

| URL | Result | Evidence boundary |
|---|---|---|
| `https://profiles.camfrog.com/home.php` | HTTP 302 to `https://www.camfrog.com/login.php?err=1` | The edit/home page requires an authenticated session; its current markup and form were not observed. |
| `https://www.camfrog.com/bot/profile` | HTTP 302 to `/bot/signon` | Bot Manager profile editing is behind its own sign-on gate. |
| `https://www.camfrog.com/bot/signon` | HTTP 200; page title `Camfrog Bot Manager`; Bot Name and Password fields visible | Confirms the sign-on page exists, not its authenticated features or protocol. |

Camfrog's [account safety guidance](https://www.camfrog.com/en/help-security.phtml) advises users to enter their nickname/password only in Camfrog software or on Camfrog.com. Do not ask users to provide Camfrog passwords or session cookies to ZChat's server.

### Profile update request observed in this repository

The checked-in `web_status_gui.py` records an authenticated browser capture dated 2026-10-09: the profile page's hopping box sent `POST https://profiles.camfrog.com/ajax/update_status.php` with `status` and a per-session `csrf` value embedded in `/home.php` ([endpoint and capture note](../src/features/web_status_gui.py#L80), [request implementation](../src/features/web_status_gui.py#L1666)). `perform_update()` fetches the profile page, checks the selected nickname against the observed account, extracts the CSRF token, then sends the HTTPS POST. It does not call Camfrog's desktop client or inject keyboard/mouse input. The update path stays dry-run unless explicitly confirmed; a non-empty JSON `response` is treated as an acknowledgement, not an independent read-back ([response handling](../src/features/web_status_gui.py#L1751)).

This is evidence of a ZChat-observed web request, not a Camfrog-published or supported API specification. The anonymous checks above could not inspect the authenticated form, and this research did not replay the request. Current endpoint behavior, field validation, rate limits, session scope, and external visibility are therefore unverified.

## Relationship to Status Changer and ZChat APIs

Using the profile request is a viable **candidate write path** for Status Changer when the requirement is to change `What's hopping` without disturbing desktop input. A direct HTTPS worker can avoid touching the mouse, keyboard, focus, cursor, or clipboard for the write itself. Authentication setup may still involve a user-operated browser; that setup must finish before unattended work begins.

The Camfrog profile AJAX request and the ZChat client/server API serve different boundaries:

- **Camfrog profile request:** authenticated request from the Windows worker to Camfrog, carrying the Camfrog session and CSRF protection. It would change the external profile status.
- **ZChat client/server API:** internal authenticated control-plane transport between ZChat components. The [client README](../zchat-client/README.md) says the client uses internal chat endpoints and does not connect to Camfrog's network protocol. The [control-server README](../control-server/README.md) describes its current command transport and states that Camfrog chat protocol operations are not implemented. The command model allows process/URI operations, but has no `set_status` operation ([command constraint](../control-server/src/camfrog_control_server/models.py#L81), [Windows adapter](../control-server/src/camfrog_control_server/windows_client.py#L53)).

Therefore, adding this provider would require both a local Camfrog HTTP adapter in the Windows agent and a separately governed ZChat command contract if remote control from `zchat-client` is wanted. The internal API cannot itself update Camfrog; it can only authorize/route a command to the agent that performs the provider request.

### Current control-plane boundary (verified from source on 2026-10-18)

- `POST /commands` is already the control-plane enqueue endpoint. It is protected by `require_admin`, validates an assigned account and active agent, and serializes conflicting/unknown-effect commands for the same account. It is not a Camfrog provider endpoint.
- The current command enum/database constraint supports client process and URI handoff operations only. It has no profile-status operation; `WindowsCamfrogClientProvider` returns `provider_not_configured` for unsupported operations.
- `zchat-client` currently authenticates its chat session with an agent token. Its Status page embeds the local Status Changer module. It does not call the admin-only command endpoint, and the agent token must not be promoted to admin authority to make that work.
- A future remote Status control flow must be: `zchat-client` operator authorization -> ZChat Server command/approval -> assigned Windows agent -> local Camfrog profile provider. The Camfrog session cookie and CSRF value must remain on that Windows machine and must never appear in command payloads, server logs, or the database.
- The standalone Status Changer should use that same local profile provider directly, without calling the ZChat Server. Its local credential/session and account identity gates must be the same implementation used by the agent, not a second HTTP implementation.

Adding a `set_status` command enum alone would only enqueue work; it would not implement provider execution or safe client authorization. The command endpoint should be extended only together with explicit operator authorization, an immutable account-scoped payload, local credential lookup, lease/claim validation, `EFFECT_UNKNOWN` handling, audit, and a result contract that distinguishes HTTP acknowledgement from profile read-back. Until those pieces exist, the existing admin-only API must not expose a new external status write operation.

## Identity, authorization, and outcome limits

- `PHPSESSID` is a bearer session credential. The existing UI supports pasted or browser-derived cookies, keeps the captured value in memory during use, and redacts it from diagnostics. Treat it like a password: never send it to the ZChat server, log it, place it in command-line arguments, or include it in evidence.
- The profile page exposes `_user_id` and `nick` markers. `_verify_session()` still checks only that the page looks signed in, but the shared update gate now requires both markers and compares the fresh profile nickname with the selected nickname immediately before POST ([identity parser](../src/features/web_status_gui.py#L2141), [pre-POST check](../src/features/web_status_gui.py#L1689)). Missing identity or a nickname mismatch blocks the POST. The provider user ID is not persisted or bound to a ZChat account registry, so canonical account assignment and revocation remain incomplete.
- A non-empty 2xx JSON acknowledgement is not proof that another Camfrog client or public profile displays the new value. The handler does not fetch the profile again and compare the resulting status.
- A POST timeout, 5xx response, malformed response, or missing acknowledgement can leave the effect unknown. The GUI now records `status_update_unknown`, stops the rotation, and tells the operator to check the profile before retrying; no automatic retry occurs for these outcomes. This is an operator warning, not a reconciliation workflow. Explicit 429 responses remain retryable with the existing bounded cooldown policy ([retry policy](../src/features/web_status_gui.py#L1540)).
- The REA checkout contains no Camfrog HAR/SAZ capture or authenticated profile HTML fixture. Its capture workflow inspects caller-provided historical captures offline; no browser session or proxy listener was started to obtain one. The profile read-back markup and parser therefore remain unverified.
- The browser-auth flow and current UI can require visible interaction. A strict background mode should accept a previously authenticated, identity-verified session and must not open/focus a browser or fall back to Camfrog UI automation while a worker is active.

## Recommendation

Prefer the profile HTTPS provider for `What's hopping` over the Camfrog-window Enter path when strict desktop isolation is required, but classify it as **IMPLEMENTED_NOT_VERIFIED** for provider support and **NOT production-ready** until all of these gates have evidence:

1. Confirm current endpoint behavior through a documented/approved provider contract, or through a single explicit operator-authorized test against an account the operator controls; do not use anonymous or stale captures as live proof.
2. Bind the ZChat account mapping to the profile's verified external user ID and nickname, persist/revoke that binding under authorization audit, and invalidate it on account switch. The local Web Status gate now rechecks the selected nickname immediately before every write, but it does not yet establish a ZChat registry binding.
3. Keep session credentials local to the Windows agent in an OS-protected store, never in ZChat server requests or logs; apply explicit per-account write authorization, resource locking, cancellation, and audit.
4. Verify the changed field by a fresh profile read and, where feasible, an independent controlled Camfrog client/profile observation. Report acknowledgement, read-back, and public visibility as separate evidence states.
5. Keep POST timeout outcomes `EFFECT_UNKNOWN`; do not automatically repeat a non-idempotent write without reconciliation.
6. Test the packaged standalone EXE on supported Windows with the Camfrog desktop foreground unchanged, cursor/clipboard/input unchanged, a minimized/closed Camfrog client, session expiry, account switching, throttling, and network interruption.

Until those gates pass, the existing Camfrog UI-driven Status Changer must not claim strict-background support, and the profile provider must not report `VERIFIED_APPLIED` based only on an HTTP acknowledgement.
