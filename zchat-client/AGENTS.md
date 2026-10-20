# AGENTS.md — zchat-client

Local rules for this subtree (stricter than root):
- The client speaks an internal protocol only; it never speaks Camfrog's network protocol.
- Fail closed on missing or ambiguous safety, security, or measurement inputs.
- Never log or commit secrets, tokens, real account names, or personal data; use documented placeholders.
- Windows-only features are BLOCKED on Linux; label them as such, never as passed.
- Do not weaken CI, branch protection, or security scanning.
