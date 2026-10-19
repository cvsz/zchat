# ZChat Control Server

ZChat Control Server is an independent ZEAZDEV product for operator-managed accounts and Windows agents. Camfrog is a third-party provider integration. ZChat is not affiliated with or endorsed by Camfrog. The integration uses a separately installed Camfrog client and sanitized browser/client observations; it does not modify or redistribute Camfrog software, impersonate Camfrog servers, automate CAPTCHA, or execute purchases.

## Implemented now

- PostgreSQL-backed FastAPI service with Alembic migrations, `/healthz`, and database-aware `/readyz`.
- Bootstrap admin token exchanged for a short-lived HS256 dashboard JWT. The dashboard keeps the JWT in memory only. Agent bearer tokens are scoped to one agent, issued once, and stored as SHA-256 hashes.
- Account-scoped operator/agent chat transport with message-body validation, idempotency keys, paginated history, leased agent polling, and explicit delivery acknowledgements. This is an internal ZChat control-plane protocol, not a Camfrog network chat protocol.
- Same-origin dashboard for agents, accounts, snapshots, commands, Cloud Bot status, imported Store metadata, and audit history.
- Account-to-agent one-to-one assignment, heartbeat, leased command queue, command results, revocation, and append-only-by-API audit events. Expired command claims become `effect_unknown` and are not redelivered automatically.
- Case-insensitive, Unicode-normalized account nickname claims prevent new duplicate account records. Migration `0006_account_nickname_claims` reserves existing names without deleting or rewriting legacy account rows; duplicate legacy rows therefore remain visible until an operator reconciles them. Nickname-based presence lookup returns HTTP 409 while a preserved duplicate makes the requested account ambiguous.
- Opt-in Windows provider for launching/stopping its own Camfrog process and opening official `join_room`, `add`, and `im` URI handlers. URI handoff is reported as `effect_unknown` with code `handoff_accepted`; it does not prove Camfrog completed the action.
- Account-scoped process and room snapshots are separate from agent, login, and human presence states. Process state only reflects the child process launched by that agent; missing or stale reports become `unknown`.
- Allowlisted profile projection and auth-state detector using `.nav-user-logged`, `external_data.type`, login-form visibility, and a CSRF-present boolean. Raw `external_data`, cookies, CSRF values, payment data, and CAPTCHA responses are discarded.
- Friend-entry normalization and separate friend/presence snapshots. Presence expires after ten minutes; status-image evidence stays `UNKNOWN` until calibrated.
- Cloud Bot status request/response helper and lifecycle snapshots. Live authenticated transport is not configured because the runtime response/auth contract remains unverified.
- Operator-imported Store catalog and an explicit purchase-consent record. Confirmation records consent only; there is no checkout or purchase executor.
- Durable subscription baseline plus non-overlapping temporary Pro/Extreme/Gold overlays. Legacy Pro lifetime entries retain their evidence source, such as `operator_report`, rather than being treated as official Camfrog verification.
- JSON-line request logs that omit query strings, headers, and bodies; common secret assignments are redacted. A bounded in-process sliding-window API limiter is enabled.
- Dockerfile and Compose stack for local PostgreSQL-backed development.

## Evidence and limits

The available browser and source observations confirm the authenticated-state markers and profile AJAX endpoint inventory, while the login POST fields and intermediate redirect chain remain unconfirmed. The profile and friends parsers are projection seams; they do not fetch Camfrog pages or replay a browser session. A local Windows adapter must supply sanitized values from an authorized official-client/browser session.

Do not treat this subproject as production-ready. The rate limiter is per process and is not shared across workers. Windows process/URI behavior has unit coverage but no Windows runtime acceptance. Public status-image semantics, Cloud Bot RPC envelope, Store response, multi-ID isolation, PostgreSQL deployment, secret rotation, backup/restore, and soak evidence remain open.

Expired command claims become terminal `effect_unknown` outcomes and are not automatically retried, because the provider may have acted before losing connectivity. URI handoff commands also use `effect_unknown` after Windows accepts the URI. An administrator can use `POST /commands/{id}/reconcile` only after checking external evidence; the endpoint records an operator attestation and evidence SHA-256, changes the state to `operator_reconciled`, and clears the account barrier. This is not provider verification: the control server does not fetch the evidence, verify its source, or claim the external effect succeeded. The JWT uses the shared `operator` principal, so this audit is not per-person attribution. Reconciliation never retries the old command. Revoking an agent cancels pending commands and marks claimed commands `effect_unknown` while invalidating stored claim hashes. Revocation cannot interrupt a provider effect already running on that machine. Internal chat delivery uses bounded at-least-once retries while claims remain eligible; if the retry limit is reached, the agent is revoked, or account ownership changes during a claim, the final state is `delivery_unknown` because the callback may have accepted the message before acknowledgement was lost. This state is not proof of delivery or failure. The service does not collect Camfrog passwords, browser profiles, session cookies, CSRF values, payment tokens, CAPTCHA answers, or room access keys as structured account fields. Message bodies are user-provided opaque text and may still contain sensitive content; follow the chat data policy below.

## Local setup

Use Python 3.12 through 3.14. Create an isolated environment and install the project:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test,lint]'
```

For a fresh local stack, generate credentials outside the repository, under the current user's configuration directory. If a PostgreSQL volume or control server already exists, keep using its existing secret source; do not generate new credentials for that volume. The running local stack used during this implementation was left untouched.

```bash
python -m camfrog_control_server.local_setup
docker compose --env-file "$HOME/.config/zchat-control/.env" up --build
```

The setup script generates distinct 256-bit secrets and refuses to overwrite an existing credential file. On Linux and WSL it creates the file with owner-only permissions in the native home directory. On Windows, the file inherits the current user's profile ACL. Keep this file out of source control and logs.

The application intentionally does not create tables at startup; Compose applies the Alembic migrations before starting the API. `/healthz` checks that the process responds; `/readyz` checks the database connection. Interactive API docs and OpenAPI are disabled by default and can be enabled by changing the generated local config.

The optional Redis service is available with `docker compose --env-file "$HOME/.config/zchat-control/.env" --profile cache up`; Redis is not currently used by the API. The Compose port binds to loopback.

The read-only ZChat–zWorkforce capability handshake and its evidence limits are documented in [ZWORKFORCE-INTEGRATION-V1.md](docs/ZWORKFORCE-INTEGRATION-V1.md).

## API outline

All routes are under `/api/v1` unless shown otherwise.

| Route | Access | Purpose |
|---|---|---|
| `POST /auth/session` | Bootstrap bearer token | Exchange bootstrap token for a short-lived dashboard JWT |
| `GET /overview` | Admin JWT | Dashboard summary |
| `POST /agents` | Admin JWT | Create an agent and issue an initial one-time token |
| `GET /agents` | Admin JWT | List agents without their tokens |
| `POST /agents/{id}/revoke` | Admin JWT | Revoke an agent, cancel pending commands, and mark claimed commands `effect_unknown` |
| `POST /agents/{id}/rotate-token` | Admin JWT | Rotate an active agent token and return the replacement once |
| `POST /accounts`, `GET /accounts` | Admin JWT | Create and list account metadata; an assigned-agent token is returned once |
| `PATCH /accounts/{id}/agent` | Admin JWT | Assign or unassign one account; assignments rotate the agent token |
| `POST /commands`, `GET /commands` | Admin JWT | Queue and inspect commands |
| `POST /commands/{id}/reconcile` | Admin JWT | Record an external-evidence-backed operator attestation for an `effect_unknown` command; does not verify provider state or retry the command |
| `POST /messages` | Admin JWT | Queue an operator message for an account's assigned agent |
| `GET /accounts/{id}/messages` | Admin JWT | Read chronological account history; use the oldest ID in a page as `before` to fetch older messages |
| `GET /audit` | Admin JWT | Read audit history |
| `POST /agent/heartbeat`, `GET /agent/commands/next` | Agent token | Heartbeat and claim a leased command |
| `GET /integrations/zworkforce/v1/capabilities` | Scoped zWorkforce service token | Negotiate the read-only integration protocol and inspect tested capability claims |
| `POST /integrations/zworkforce/v1/mcp` | Scoped zWorkforce service token | MCP `2026-07-28` read-only tools/resources for capabilities and health |
| `GET /agent/chat/session` | Agent token | Read the assigned account nickname and a read-only subscription summary without refreshing command-agent presence |
| `POST /agent/commands/{id}/result` | Agent token | Complete a command using its one-time claim token |
| `POST /agent/messages` | Agent token | Submit a message for the agent's assigned account |
| `GET /agent/messages/next` | Agent token | Claim the next operator message with a bounded lease |
| `POST /agent/messages/{id}/ack` | Agent token | Acknowledge delivery using the claim token |
| `POST /agent/accounts/snapshot` | Agent token | Submit a sanitized profile/auth/friends snapshot |
| `GET /accounts/{id}/profile`, `GET /accounts/{id}/friends`, `GET /accounts/{id}/runtime` | Admin JWT | Read snapshots reported by the assigned agent |
| `GET /accounts/{id}/auth-state`, `GET /presence/{nickname}` | Admin JWT | Read independent auth and presence state |
| `GET /cloud-bots`, `GET /cloud-bots/{id}/status` | Admin JWT | Read Cloud Bot registry and last reported state |
| `GET /store/catalog/items` | Admin JWT | Read operator-imported catalog metadata |

`POST /commands` accepts only its declared command schema and rejects unknown fields with HTTP 422. It is currently an admin-JWT control-plane route; it does not accept or verify zWorkforce approval grants. The zWorkforce integration remains read-only, and generic AI services must not call this endpoint as an execution shortcut.

The MCP endpoint implements the zWorkforce client's `2026-07-28` stateless HTTP contract, including protocol metadata and matching `Mcp-Method`/`Mcp-Name` headers. It exposes only `zchat.capabilities` and `zchat.health`, plus `zchat://capabilities` and `zchat://health` resources. It exposes no account, worker, operation, lock, or mutation data because the service token has no tenant-to-account scope. The endpoint rejects unknown protocol versions and methods.

Exchange the bootstrap token using `POST /api/v1/auth/session`; use the returned short-lived JWT for dashboard/admin routes. `POST /agents` issues an initial one-time token. Assigning that agent through `POST /accounts` or `PATCH /accounts/{id}/agent` rotates the token and returns the active one-time `agent_token` in that assignment response; install that latest token on the assigned machine. Reassignment invalidates both the previous agent token and the replacement agent's earlier token. Use `POST /agents/{id}/rotate-token` to recover a lost token without changing the account assignment; this invalidates the previous token and returns the replacement once. Account and agent list/read responses never include tokens. Revoking an agent is permanent. Restart the server with rotated `CONTROL_ADMIN_TOKEN` and `CONTROL_JWT_SECRET` values to invalidate dashboard sessions.

## Windows agent

Run the polling agent from an interactive terminal. It asks for the one-time token without echoing it and keeps the token in process memory only. Supply the verified HTTPS origin of the running control server and the installed official client path through your local environment; the agent does not assume a public endpoint or client installation path.

Use `python -m camfrog_control_server.agent --help` to review the supported arguments.

The remote server URL must use HTTPS. Plain HTTP is accepted only for loopback development addresses. The sample rejects redirects, does not persist tokens, and uses jittered exponential backoff for transport failures. `launch`, `stop`, `restart`, `join_room`, `add_friend`, and `open_im` are supported by the local provider. `leave_room` remains disabled until an official action is verified.

## Internal chat transport

Operator messages use `POST /api/v1/messages` with an account ID, a client-generated UUID `client_message_id`, and a UTF-8 text body of 1 to 4,000 characters. The server scopes each message to the account's currently assigned, enabled agent. Repeating the same ID and body returns the original message; reusing the ID with different content returns `409 Conflict`.

Agents claim operator messages from `GET /api/v1/agent/messages/next`. A claim token is returned once, stored only as a hash, and must be supplied to the acknowledgement endpoint. Expired claims are eligible for bounded redelivery; delivery is therefore at least once, and agent-side handlers should deduplicate using the message ID before acknowledging. Exhausted claims, revoked-agent claims, and claims invalidated by account reassignment become `delivery_unknown`; the callback may have accepted a message before its acknowledgement was lost. This terminal state is neither success nor failure evidence. Agents submit messages in the reverse direction through `POST /api/v1/agent/messages`, using a client-generated idempotency ID. Admin history is account-scoped, returned oldest to newest within each page, and paginated by passing the oldest message ID in the current page as `before`.

The HTTP contract, agent transport helpers, and API tests are implemented. The reference polling loop consumes messages only when a provider exposes `receive_chat_message`; the current Windows Camfrog adapter does not implement that method or inject messages into the desktop client. A claim/ack proves that a provider accepted the message only; it does not prove that a Camfrog user saw or sent it. No Camfrog private wire protocol is implemented or claimed.

`GET /agent/chat/session` returns only the requesting agent's enabled account nickname and a sanitized subscription summary (tier, evidence source, record type, and active overlay expiry). Evidence notes and other accounts' subscription records are not exposed. The summary is a control-plane record, not live Camfrog verification and not an entitlement grant.

## Deployment boundaries

- Put the API behind TLS and restrict network access to the operator dashboard/API and registered agents.
- Use a managed secret store for `CONTROL_ADMIN_TOKEN`, `CONTROL_JWT_SECRET`, and database credentials. Rotate both auth secrets together to invalidate sessions.
- Keep request-body logging disabled at the reverse proxy. The application logs method, path, status, request id, and duration only.
- Keep the agent token local to its assigned Windows machine. The sample's interactive prompt does not provide non-interactive Windows secret-store integration.
- The read-only zWorkforce capability endpoint is disabled unless `CONTROL_ZWORKFORCE_INTEGRATION_TOKEN` is configured with at least 32 random characters. Configure the matching value in zWorkforce's server-side `ZCHAT_INTEGRATION_TOKEN`; never put it in browser/static configuration. No ZChat mutation is exposed through this integration endpoint.
- Use a dedicated PostgreSQL role with access only to this database. Back up and protect audit and account metadata according to your retention policy.
- Stop the control-server API before applying migration `0006_account_nickname_claims`. The migration takes a PostgreSQL exclusive lock on `accounts` while it backfills nickname claims; keep legacy API writers stopped until the new server version is running.
- Apply migration `0007_unknown_command_outcomes` before deploying the API build that writes `effect_unknown`. Upgrade Windows agents with the server: this version rejects `succeeded` reports for URI handoffs, so older agents fail closed until updated.
- Apply migration `0008_unknown_chat_delivery` before deploying the API build that writes `delivery_unknown`. The migration preserves the uncertain-delivery state and refuses downgrade while such rows remain.
- Set aggregate SQLAlchemy pool limits against the database's actual connection budget before increasing worker count.
- The in-process limiter does not coordinate across multiple API workers or replicas; production multi-instance deployments need a shared limiter such as Redis before scaling out.
- Gold nickname changes, settings mutations, Store checkout, and Cloud Bot control operations remain disabled. Future sensitive actions must use the official flow, show exact changes/costs, and require fresh explicit confirmation.
- Chat bodies are stored as plaintext in PostgreSQL. Do not send passwords, session material, payment data, CAPTCHA responses, or other secrets through this API. Define retention, backup access, and deletion policy before production use.

## Validation

From this directory:

```bash
pytest
ruff check src tests
docker compose --env-file "$HOME/.config/zchat-control/.env" exec control-server alembic current
```

SQLite-backed tests validate API behavior. CI applies Alembic migrations to a PostgreSQL service. These checks do not establish Windows packaging, network security, or Camfrog runtime compatibility; Windows agent/service acceptance requires separate runtime validation.
