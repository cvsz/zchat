# ZChat–zWorkforce Integration Audit

**Status:** PARTIALLY VERIFIED  
**Scope:** Source-backed design audit of `cvsz/zchat` and `cvsz/zworkforce` before runtime integration.  
**Reviewed:** 2026-10-17  
**Source revision evidence:** UNVERIFIED. Immutable reviewed commit SHAs and revision-pinned paths for both repositories were not recorded; the mutable references below do not yet establish reproducible external evidence.
**Decision:** Do not enable remote mutations or claim production integration until the blocking contracts and tests below are implemented.

## Executive summary

ZChat and zWorkforce already provide useful but separate foundations. ZChat Control Server owns account-to-agent assignment, reusable bearer agent credentials whose raw value is disclosed once, command/chat queues, and local Windows provider handoff. zWorkforce owns tenant-scoped AI tasks/workflows, policy and approvals, authenticated MCP, and durable orchestration. These are not interchangeable control planes.

The safe integration direction is:

- **zWorkforce:** planning, workflow orchestration, tenant identity, and workflow-level governance.
- **ZChat Control Server:** provider-account mapping, provider-specific authorization, local execution policy, and reconciliation of Windows/Camfrog outcomes.
- **Windows agent:** the only component allowed to cause local Windows/Camfrog effects, after fresh local checks.
- **Approval authority:** one canonical issuer/revoker and trust anchor; grant bound to integration audience, tenant, exact immutable action digest, account/resource, operator, policy version, expiry, and single-use nonce.
- **External execution:** at-least-once unless the provider itself supplies enforceable idempotency/fencing. An expired lease or accepted command is not proof of a successful external effect.

## Source-backed findings

| Area | Evidence in repository | Finding |
|---|---|---|
| ZChat operator authentication | `control-server/src/camfrog_control_server/api.py`, `require_admin` and `create_dashboard_session` | The current dashboard path exchanges a bootstrap admin bearer for a short-lived JWT. It is not a distinct integration identity with narrowly scoped read/write grants. |
| ZChat agent identity | `control-server/README.md`, API route table; `api.py`, `require_agent` | Agent tokens are hashed at rest and bound to an agent. Account assignment is a separate relationship. A token proves agent authentication, not external Camfrog account identity or authority to perform every action. |
| ZChat command semantics | `control-server/README.md`, “Evidence and limits” and “Internal chat transport” | Commands/messages may be redelivered after lease expiry. A claim/ack confirms control-plane handling, not that Camfrog completed or exposed the requested action. |
| Camfrog boundary | `control-server/README.md`, “Implemented now” and “Windows agent” | URI handoff is explicitly not proof that Camfrog joined a room or completed an action. The Windows adapter has no claimed private Camfrog wire protocol. |
| ZChat multi-instance safety | `control-server/README.md`, “Deployment boundaries” | The API rate limiter is process-local; multi-instance deployments need a shared limiter. The current command lease model does not establish cross-repository fencing for external effects. |
| zWorkforce tenant/policy model | `docs/API.md`, “Core”, “Tasks”, and “Identity / audit”; `zworkforce/mcp.py` | zWorkforce has tenant-scoped API/MCP operations and role/scope checks. Its existing generic task/workflow tools do not by themselves define ZChat provider-account authorization. |
| zWorkforce delivery semantics | `docs/API.md`, “Workflow automation” | Task/workflow execution is at-least-once after lease expiry; external mutating consumers must deduplicate their own side effects. |
| MCP transport | `docs/API.md`, “MCP”; `zworkforce/mcp.py` | The current modern MCP HTTP protocol requires protocol/version metadata and matching `Mcp-Method` / `Mcp-Name` headers. An adapter must use the documented protocol, not assume a generic JSON-RPC POST is sufficient. |
| Cross-repository governance | `AGENTS.md`, `docs/CENTER-CONTROL-PLANE.md` in zWorkforce | Ownership boundaries must remain explicit; credentials stay server-side; cross-repository changes require focused branches, exact-head checks, review, and durable evidence. The Center Control Plane document is a contract/plan, not proof of an implemented fleet integration. |

## Priority gap matrix

### P0 — block external mutations

1. **No canonical ZChat MCP contract is implemented.** Define versioned tool schemas, result/error envelopes, capability discovery, and protocol negotiation.
2. **No cross-system approval binding exists in the reviewed contract.** A zWorkforce task approval must not automatically authorize a Camfrog mutation. Bind approval to exact action, account/resource, immutable payload digest, operator, expiry, nonce, and current policy version.
3. **No enforceable shared execution authority is established.** Before an external write, ZChat must verify identity and authorization. A pre-effect lease check cannot fence a suspended GUI executor; require provider-side fencing or one designated serialized executor with reconciliation before reassignment. Otherwise disable writes.
4. **Outcome semantics are insufficient for external effects.** Separate `ACCEPTED`, `EXECUTING`, `EFFECT_UNKNOWN`, `VERIFIED_SUCCEEDED`, and `VERIFIED_FAILED`. Never translate HTTP success, queue acknowledgement, or URI handoff into provider success.
5. **No production evidence supports cross-repository or real Windows/Camfrog acceptance.** Do not enable remote mutations until exact environment evidence is recorded.

### P1 — required for a governed integration

- A machine-readable feature capability registry distinguishes configured, implemented, and live-verified capability.
- Versioned agent enrollment/heartbeat records bind tenant, operator grant, ZChat account, machine, agent instance, protocol version, and capability evidence.
- Durable operation IDs, idempotency keys, bounded retries, unknown-outcome reconciliation, and operator-review handling.
- Cross-tenant and cross-account negative tests, token revocation tests, approval replay/mismatch/expiry tests, stale-lease tests, duplicate delivery tests, and emergency-stop propagation tests.
- Correlation IDs and redacted audit events shared across zWorkforce task/workflow, ZChat command, and Windows agent.

### P2 — operational hardening

- Cross-repository contract CI that validates the same schema/version from both repositories without production secrets.
- Load/soak tests for queue contention, lease renewal failure, process restart, and network partitions.
- Release artifact provenance and rollback drills for the actual Windows package and server deployment.

## Proposed versioned protocol

Use a dedicated integration namespace and additive versioned contracts; do not repurpose the existing operator dashboard JWT as a service-to-service credential.

Common metadata for authenticated read-only operations: `protocol_version`, `tenant_id`, `correlation_id`, `request_id` and the scoped service identity. These calls do not require fabricated command, lease, approval or agent IDs.

Additional minimum fields for mutations only:

- `protocol_version`
- `tenant_id`, `operator_id`, `account_id`, `agent_instance_id`
- `command_id`, `correlation_id`, `idempotency_key`
- `action`, `resource_key`, `payload_digest`
- `approval_grant_id`, `policy_version`, `expires_at`, `nonce`
- `lease_id`, `fencing_generation`
- `state`, `observed_at`, `evidence_refs`

The server derives a canonical resource key from the authorized account/action, not an untrusted caller alias. Idempotency is transactionally scoped to authenticated tenant/principal and immutable request digest: exact retries return the original operation, changed payloads with the same key are rejected. Grants bind tenant and integration audience. Provider mutations lacking enforceable fencing must remain single-owner until the previous outcome is reconciled.

Minimum read-only operations for the first runtime slice:

- discover supported protocol and capabilities;
- inspect provider/runtime health;
- inspect sanitized feature/worker state;
- inspect current resource-lock ownership;
- inspect sanitized operation history.

Mutation operations must remain disabled until the P0 gates are implemented. Read-only operations still require an authenticated, least-privilege service identity and tenant/account scope.

## Implementation sequence

1. **Contract first:** add JSON Schemas and contract tests for capability, command, approval grant, operation state, cancellation/emergency-stop, and audit events.
2. **Read-only adapter:** add ZChat-side MCP discovery and read-only tools only, using a dedicated service identity, bounded inputs/outputs, no raw chat dumps, and audit logging.
3. **zWorkforce client/skill:** consume the versioned adapter through the existing authenticated MCP client, map tenant/operator grants explicitly, and do not expose provider credentials to browser/static clients.
4. **Approval-gated commands:** add durable command/approval binding, local pre-execution checks, lease/fencing behavior, cancellation, and unknown-outcome reconciliation.
5. **Cross-repository CI:** validate schema compatibility and negative authorization cases without production credentials.
6. **Windows acceptance:** test on the exact supported Windows/Camfrog runtime and record foreground focus, keyboard/mouse/clipboard, process identity, actual external state, and artifact hashes.
7. **Release gate:** require independent review, exact-head green required checks, signed/provenance evidence where configured, rollback readiness, and explicit release authorization.

## Acceptance gates

| Gate | Current status | Evidence required to close |
|---|---|---|
| Existing ZChat control-plane authentication and agent scoping | PARTIALLY VERIFIED | Negative API tests for privilege escalation and account reassignment/revocation on the exact candidate SHA |
| zWorkforce tenant/scope enforcement | PARTIALLY VERIFIED | Integration-level tenant/account isolation and invalid credential tests |
| Versioned ZChat MCP contract | BLOCKED | Implementation absent; schema, server/client implementation, contract tests, protocol negotiation tests |
| Approval bound to exact external action | BLOCKED | Implementation absent; digest/nonce/expiry/replay/mismatch tests and durable audit evidence |
| Single writer and fencing across agents | UNVERIFIED | Real concurrent multi-agent tests proving stale generations cannot start effects |
| External outcome reconciliation | UNVERIFIED | Provider-specific evidence for success/failure/unknown outcomes; no acknowledgement-only shortcut |
| Strict background Windows execution | UNVERIFIED | Live Windows acceptance on the target Camfrog version |
| Cross-repository contract CI | BLOCKED | Implementation absent; CI run validating both repositories against the same versioned contract |
| Integration readiness | BLOCKED | All integration gates verified; production readiness separately requires operations, deployment, backup/restore, monitoring, security and release evidence |

## Scope and validation notes

This is an audit/design artifact, not a claim that the runtime integration has been implemented. Repository source and documentation were inspected through GitHub. Local commands, full test suites, Windows runtime acceptance, production connectivity, signing, and live Camfrog verification were not run as part of this audit. Those remain explicit blockers rather than inferred successes.
