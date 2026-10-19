# Camfrog Cloud Bot Manager: public-source research

Researched 2026-10-13. Sources are Camfrog's own public pages and anonymous HTTPS GETs. No login, cookies, credentials, or form submissions were used.

Follow-up on 2026-10-18: after operator authorization, one read-only GET using the local development `WEBAUTH_BOT_USER` cookie loaded the authenticated `/bot/profile` form. No account values were recorded and no form was submitted. This confirms the current local cookie can read that page; it does not establish a supported API contract or verify any Bot Manager mutation.

Follow-up on 2026-10-19: one operator-authorized sign-on attempt used the existing ignored `.env` keys `CAMFROG_BOT_USER` and `CAMFROG_BOT_PASSWORD` against the official HTTPS `/bot/signon` form. The response remained on `/bot/signon`; a `cloudbot` cookie was set, but this did not establish an authenticated session. The attempted credentials were not retried, `/bot/room` was not fetched in this attempt, and no room action was submitted. The public form exposes `_1` and `_2` text/password inputs and a submit button; no CAPTCHA marker appeared in the anonymous page markup, which does not rule out server-side verification. The application code does not consume the `CAMFROG_BOT_USER` or `CAMFROG_BOT_PASSWORD` keys; its Bot Manager flow accepts a `cloudbot=<token>` cookie via `WEBAUTH_BOT_USER`.

## Public route checks

| Route | Anonymous result | What this establishes |
|---|---|---|
| [`/bot/room`](https://www.camfrog.com/bot/room) | HTTP 302 to [`/bot/signon`](https://www.camfrog.com/bot/signon) | The public request reaches a sign-on gate; room fields and actions were not visible. |
| [`/bot/profile`](https://www.camfrog.com/bot/profile) | HTTP 302 to [`/bot/signon`](https://www.camfrog.com/bot/signon) | The public request reaches a sign-on gate; profile fields and actions were not visible. |
| [`/bot/plugins/`](https://www.camfrog.com/bot/plugins/) | HTTP 404 | The unauthenticated plugin index did not resolve. This does not establish what an authenticated session can access. |
| [`/bot/plugins/1`](https://www.camfrog.com/bot/plugins/1) | HTTP 302 to [`/bot/signon`](https://www.camfrog.com/bot/signon) | The public request reaches the sign-on gate; the plugin form and its schema were not visible. |

The [public sign-on page](https://www.camfrog.com/bot/signon) labels the service “Camfrog Bot Manager,” says “Manage your bot remotely,” and displays Bot Name and Password fields. It does not document the `cloudbot` cookie, endpoint methods, form fields, or an API contract. Those details therefore remain unverified by public documentation.

## Product features and version boundary

- Camfrog's [Cloud Bot 2.0 announcement](https://blog.camfrog.com/new-camfrog-cloud-bot/) names DJ Manager, Auto Moderator, and Who Banned, and says the update added support for room features missing from the older bot.
- The [Cloud Bot 2.1 announcement](https://blog.camfrog.com/camfrog-cloud-bot-2-1/) describes a Who Banned option to notify moderators only and an option to restrict regular users from taking the microphone.
- The [Camfrog Bot Owner Guide](https://www.camfrog.com/en/camfrogbot.phtml) documents the older desktop Camfrog Bot and its plug-ins. It says custom functionality can be added by writing plug-in modules, but it does not document the remote Bot Manager routes.
- Camfrog's [Legacy Bot Sunset notice](https://blog.camfrog.com/legacy-bot-sunset/) says the standalone bot would stop being compatible with room software on February 1, 2023, with Cloud Bot as the supported bot. Treat the desktop guide's plug-in model as legacy product information, not as evidence for Cloud Bot Manager behavior.

## GUI design reference

Extron's [GUI Design Themes guide](https://www.extron.com/article/guidesign) is the design reference for this revision. It frames GUI design around information mapping, color, ergonomics, and fonts. Its [GUI Standards Guide](https://media.extron.com/public/download/files/guides/gui_standards_guide_C.pdf) emphasizes predictable navigation and consistency in visual controls and placement. The Web Status redesign applies those principles with a fixed Room/Profile/Plugins navigation rail, a searchable responsive plugin-card grid, separate plugin settings space, and explicit refresh/apply actions with read-back feedback. This is a design reference; it does not document Camfrog's web endpoints.

## Relation to this checkout

The current client in [`web_status_gui.py`](../src/features/web_status_gui.py#L252) sends a `cloudbot` cookie to `www.camfrog.com`; it parses plugin links under `/bot/plugins/<id>` and sends a JSON `switch_plugin` request to `/bot/ajax/` ([client parser and switch call](../src/features/web_status_gui.py#L312)). These are implementation details in this repository, not endpoint semantics documented by Camfrog's public pages.

[`IMPLEMENTATION_SUMMARY.md`](../IMPLEMENTATION_SUMMARY.md#L13) records an authenticated, read-only page check from the implementation work. This research did not repeat that check, so the reported Room/Profile forms and plugin inventory are not independently verified here. Public sources do not confirm the exact plugin IDs, `/bot/ajax/` RPC shape, save behavior, or the `/bot/stop` and `/bot/logout` actions.

## Conclusion

Public first-party evidence describes Cloud Bot product features and a remote Bot Manager sign-on portal. Anonymous route checks show the Room/Profile URLs and the tested numeric plugin URL redirect to sign-on, while the plugin index URL returns 404. These checks do not reveal or prove the authenticated plugin endpoint. Authenticated page contents and request protocol remain unverified without an authorized session or an official API specification.
