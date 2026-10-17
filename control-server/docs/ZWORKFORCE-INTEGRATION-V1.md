# zWorkforce Integration v1: Read-Only Resources

ZChat exposes a read-only capability endpoint for the authenticated zWorkforce
service:

```text
GET /api/v1/integrations/zworkforce/v1/capabilities
Authorization: Bearer <CONTROL_ZWORKFORCE_INTEGRATION_TOKEN>
```

The same token scopes a non-account-scoped health resource:

```text
GET /api/v1/integrations/zworkforce/v1/health
```

The control server also exposes a stateless MCP endpoint compatible with the
current zWorkforce `RemoteMCPClient`:

```text
POST /api/v1/integrations/zworkforce/v1/mcp
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/list | tools/call | resources/list | resources/read
Authorization: Bearer <CONTROL_ZWORKFORCE_INTEGRATION_TOKEN>
```

It lists two read-only tools (`zchat.capabilities`, `zchat.health`) and two
read-only resources (`zchat://capabilities`, `zchat://health`). The client and
server must provide matching protocol metadata and method/name headers. No
account-scoped data or mutating tool is exposed.

The protocol identifier is `zchat-zworkforce`, version `1.0`. The token is
optional and the endpoint returns `503` until configured. Tokens shorter than
32 characters are rejected. Capabilities return metadata only; health reports
database reachability and returns only `{"status":"healthy","database":"reachable"}`
when the database probe succeeds. Version/resource/evidence lists contain
unique, non-empty strings. Worker and operation inventory is withheld until
authenticated tenant-to-account identity mapping exists: the global service
token cannot safely expose cross-tenant runtime or command data. No
resource-lock authority is implemented.

The capability response lists the eight Camfrog standalone features and the
separately packaged LINE status changer. `standalone_supported` means a source
entry point and build target exist; it is not a live Windows acceptance claim.
`unified_supported` is true for Private Chat and for the Random, Marquee, and
combined Status Changer feature IDs. Private Chat is embedded as a read-only
manager; the shared Status Changer page contains Random and Marquee tabs. It
remains false for the other five features.
`supports_remote_commands`, strict background, minimized/hidden client support,
and external outcome verification are false for every listed feature. All
entries report `PARTIALLY VERIFIED` evidence and empty verified
platform/provider-version lists. Do not upgrade these claims based on mocks,
source declarations, or artifact presence alone.

The zWorkforce client accepts HTTPS origins; HTTP is allowed only for loopback
development. It rejects redirects and incompatible protocol versions. Configure
the same random service token in zChat's
`CONTROL_ZWORKFORCE_INTEGRATION_TOKEN` and zWorkforce's
`ZCHAT_INTEGRATION_TOKEN`, with `ZCHAT_CONTROL_URL` set to the HTTPS origin.
Keep the token in server-side secret configuration.

These endpoints are read-only observability only. Agent/worker inventory,
account-scoped operation history, resource-lock ownership, command submission,
canonical approval verification, execution leases/fencing, emergency stop, and
outcome reconciliation are not provided by this integration version. These
gates require tenant-to-account identity binding and an enforceable execution
authority before they can be exposed safely.
