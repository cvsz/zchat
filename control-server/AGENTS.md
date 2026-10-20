# AGENTS.md — control-server

Local rules for this subtree (stricter than root):
- Loopback (127.0.0.1) is the default bind. Never bind to 0.0.0.0 by default.
- No Camfrog network protocol is spoken here; this server is an internal control plane only.
- Fail closed on missing or ambiguous safety, security, or measurement inputs.
- Never log or commit secrets, tokens, or personal data.
- CORS must be explicit; no wildcard origins in production.
