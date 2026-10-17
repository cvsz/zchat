"""Polling reference agent with an opt-in official-client provider."""

from __future__ import annotations

import argparse
import getpass
import random
import sys
import time
from datetime import UTC, datetime
from typing import Any

from .transport import (
    AgentTransportError,
    ControlPlaneClient,
)
from .transport import (
    validate_server_url as validate_server_url,
)
from .windows_client import WindowsCamfrogClientProvider

COMMAND_LEASE_PREFLIGHT_RESERVE_SECONDS = 5.0


def _has_command_lease_window(command: dict[str, Any], request_elapsed_seconds: float) -> bool:
    """Require enough server-issued lease time to remain after the poll round trip."""
    try:
        issued_at = datetime.fromisoformat(str(command["claim_issued_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(command["claim_expires_at"]).replace("Z", "+00:00"))
        if issued_at.tzinfo is None or expires_at.tzinfo is None:
            return False
        lease_seconds = (expires_at.astimezone(UTC) - issued_at.astimezone(UTC)).total_seconds()
        elapsed = float(request_elapsed_seconds)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if elapsed < 0:
        return False
    return lease_seconds - elapsed >= COMMAND_LEASE_PREFLIGHT_RESERVE_SECONDS


class NoCamfrogProvider:
    """Fail closed when no local official-client provider has been configured."""

    def execute(self, _command: dict[str, Any]) -> tuple[str, str]:
        return "failed", "provider_not_configured"

    def process_state(self) -> str:
        return "unknown"


def poll_once(client: ControlPlaneClient, provider: Any) -> bool:
    process_state = getattr(provider, "process_state", None)
    heartbeat: dict[str, str] = {"state": "running"}
    if callable(process_state):
        heartbeat["client_process_state"] = process_state()
    client.request("POST", "/agent/heartbeat", heartbeat)
    command_poll_started = time.monotonic()
    response = client.request("GET", "/agent/commands/next")
    command_poll_elapsed = time.monotonic() - command_poll_started
    command = (response or {}).get("command")
    did_work = False
    if command is not None:
        if _has_command_lease_window(command, command_poll_elapsed):
            state, code = provider.execute(command)
            client.request(
                "POST",
                f"/agent/commands/{command['id']}/result",
                {"claim_token": command["claim_token"], "state": state, "code": code},
            )
            did_work = True
        else:
            print("Command lease is stale or malformed; refusing local execution.")

    receive_chat_message = getattr(provider, "receive_chat_message", None)
    if callable(receive_chat_message):
        message = client.poll_chat_message()
        if message is not None:
            try:
                delivered = receive_chat_message(message)
            except Exception as exc:
                # Leave the lease unacknowledged so the server can retry without logging content.
                print(f"Chat delivery failed ({type(exc).__name__}); the lease will expire.")
            else:
                if delivered is True:
                    client.acknowledge_chat_message(message["id"], message["claim_token"])
                    did_work = True
    return did_work


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the reference control-plane polling agent.")
    parser.add_argument(
        "--server-url",
        required=True,
        help="HTTPS origin of the running control plane (HTTP is limited to loopback)",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="Send one heartbeat and poll once.")
    parser.add_argument(
        "--client-executable",
        help="Opt-in absolute path to the installed official Camfrog .exe (Windows only).",
    )
    args = parser.parse_args()
    if not 1.0 <= args.poll_seconds <= 60.0:
        parser.error("--poll-seconds must be between 1 and 60.")

    try:
        token = getpass.getpass("Agent token (kept in memory only): ")
        client = ControlPlaneClient(args.server_url, token)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        if args.client_executable:
            provider = WindowsCamfrogClientProvider(
                args.client_executable, platform_name=sys.platform
            )
        else:
            provider = NoCamfrogProvider()
    except ValueError as exc:
        parser.error(str(exc))
    failures = 0
    try:
        while True:
            try:
                poll_once(client, provider)
                failures = 0
                delay = args.poll_seconds
            except AgentTransportError as exc:
                # Never print request headers, tokens, or response bodies.
                print(f"Agent transport: {exc}")
                failures += 1
                delay = min(60.0, args.poll_seconds * (2 ** min(failures - 1, 6)))
            if args.once:
                return
            time.sleep(random.uniform(delay * 0.8, delay * 1.2))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
