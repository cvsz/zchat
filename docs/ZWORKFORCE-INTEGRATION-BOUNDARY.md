# ZChat / zWorkforce integration boundary (proposal)

Status: PROPOSED — no runtime integration or production approval implied.

External source revision: UNVERIFIED. Immutable commit IDs and revision-pinned paths for both repositories must be recorded before treating the cross-repository claims as reproducible evidence.

## Source-backed baseline

- ZChat README identifies a Windows-only Camfrog UI Automation application.
- ZChat control-server README describes a separate loopback-bound local MVP, not production.
- zWorkforce AGENTS.md requires tenant isolation, bounded execution, explicit mutation authorization and durable state.
- zWorkforce's external production evidence stages A–I are pending. Neither repository's documentation proves an end-to-end Camfrog mutation path.

## Ownership

| Boundary | Authority |
| --- | --- |
| zWorkforce | Workflow planning, orchestration, bounded intent and approval references |
| Canonical approval authority | Sole issuer and revoker of signed grants, key rotation and revocation state. Until configured and independently verified, external mutation stays disabled. |
| ZChat Control Server | Provider-account mapping, action authorization, durable operation state, resource leases and audit |
| ZChat Windows Agent | Process identity checks, actual Windows effects, outcome verification |
| Official Camfrog client | External application; no claimed provider-enforced fencing or exactly-once semantics |

No generic zWorkforce tool may directly control the Windows desktop or invoke a Camfrog write action.

## Minimum versioned command envelope

A future `zchat.command.submit.v1` request MUST carry:
- `schema_version`, `command_id`, `idempotency_key`, `correlation_id`
- `tenant_id`, `operator_id`, `provider_account_id`, `resource_key`
- `action`, `payload_sha256`, `approval_reference`, `policy_version`
- `requested_at`, `expires_at`

Server-side authentication, account authorization, approval authenticity and expiry MUST be verified independently of user-supplied fields. Never trust an arbitrary caller-provided `approved: true` flag. Sensitive tokens must not appear in event payloads or logs.

The server MUST scope each `idempotency_key` to the authenticated tenant and principal and transactionally store the canonical request digest. Identical retries return the original operation; reuse with different content fails. The server derives a canonical resource key from the authorized provider account and action; caller aliases cannot create independent leases.

Approval verification MUST bind issuer, audience, tenant, operator, account, canonical resource, action, the SHA-256 of the exact bytes to execute, policy version, deadline and single-use nonce. Verify signature, revocation, full field equality and nonce consumption before execution. Validate server-time expiration at admission, claim and immediately before effect, transitioning expired commands to non-executable `EXPIRED`.

## Execution state and split-brain handling

`CREATED -> AUTHORIZED -> QUEUED -> LEASED -> EXECUTING -> EFFECT_UNKNOWN | VERIFIED_SUCCEEDED | VERIFIED_FAILED`.

`CANCELLED` is allowed only before an external effect is possible; `EXPIRED` is the durable pre-effect deadline outcome. An interrupted execution moves to `EFFECT_UNKNOWN` unless independent evidence proves no effect occurred.

- The server owns atomic claims and monotonic lease generations.
- The Windows executor checks the active owner immediately before every external effect.
- A pre-effect lease check does not fence the Camfrog GUI across a lease expiry or network partition. Without provider-enforced fencing, assign one designated executor per resource and prohibit reassignment until the previous in-flight effect is reconciled or conclusively absent. Otherwise disable the mutation.
- If the server is unavailable or ownership cannot be proven, deny new writes.
- If a non-idempotent external effect may have happened, record `EFFECT_UNKNOWN` and require reconciliation; never blindly retry.
- A lease alone does not establish exactly-once behavior in the official Camfrog GUI.
- Emergency stop revokes new execution authorization, stops ZChat workers, preserves audit records and does not terminate the official Camfrog client.

## Incremental implementation gates

1. Inventory existing ZChat server APIs, database migrations, Windows client execution paths and zWorkforce adapters. Preserve working code.
2. Add typed versioned schemas and negative contract tests without enabling mutations.
3. Implement authenticated read-only capability discovery with tenant-scoped access.
4. Add canonical account identity, approval verification, durable claims, local resource ownership and fencing checks.
5. Add explicit operator-approved mutation adapters, audit/outbox, cancellation and outcome reconciliation.
6. Validate on genuine Windows 11 and Camfrog 8.5.0.51219: account switching, process restarts, no focus/mouse/keyboard/clipboard/sound interference, network partition and split-brain attempts.
7. Run existing repository CI, security scanning and real deployment acceptance. Obtain independent review before protected merge.

## Release status

`NOT_IMPLEMENTED`: versioned cross-repository runtime adapter and actual end-to-end acceptance.

`BLOCKED`: live Camfrog Windows acceptance and production deployment evidence are unavailable from GitHub source inspection alone.

This document is a boundary proposal, not a claim that the complete master prompt has been implemented.
