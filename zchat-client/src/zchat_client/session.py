"""Threaded chat session that keeps network I/O away from the UI thread."""

from __future__ import annotations

import queue
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from camfrog_control_server.schemas import clean_chat_body
from camfrog_control_server.transport import AgentTransportError, ControlPlaneClient


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    """A sanitized event for the desktop UI."""

    kind: str
    data: dict[str, Any]


ClientFactory = Callable[[str, str], ControlPlaneClient]
MAX_PENDING_SENDS = 100
MAX_REMEMBERED_MESSAGE_IDS = 10_000


class AgentChatWorker:
    """Run assignment refresh, chat polling, sends, and acknowledgements on one worker thread."""

    def __init__(
        self,
        server_url: str,
        agent_token: str,
        *,
        client_factory: ClientFactory = ControlPlaneClient,
        poll_interval: float = 2.0,
        assignment_refresh_interval: float = 30.0,
        retry_base: float = 1.0,
        retry_max: float = 60.0,
    ) -> None:
        if not 0.2 <= poll_interval <= 60:
            raise ValueError("Poll interval must be between 0.2 and 60 seconds.")
        if not 1 <= assignment_refresh_interval <= 300:
            raise ValueError(
                "Assignment refresh interval must be between 1 and 300 seconds."
            )
        if not 0.01 <= retry_base <= retry_max <= 300:
            raise ValueError("Retry delays must be between 0.01 and 300 seconds.")
        self._server_url = server_url
        self._agent_token = agent_token
        self._client_factory = client_factory
        self._poll_interval = poll_interval
        self._assignment_refresh_interval = assignment_refresh_interval
        self._retry_base = retry_base
        self._retry_max = retry_max
        self.events: queue.SimpleQueue[WorkerEvent] = queue.SimpleQueue()
        self._commands: queue.SimpleQueue[tuple[str, str, str, str | None]] = (
            queue.SimpleQueue()
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._accepted_lock = threading.Lock()
        self._accepted_ids: set[str] = set()
        self._accepted_order: deque[str] = deque()
        self._pending_send_lock = threading.Lock()
        self._pending_send_count = 0
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_alive:
            raise RuntimeError("Chat worker is already running.")
        if self._started:
            raise RuntimeError(
                "Chat worker sessions cannot be restarted; create a new session."
            )
        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="zchat-agent-session", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def submit_message(self, body: str, account_id: str | None) -> str:
        """Queue a validated message for the account selected at submission time."""
        if not isinstance(body, str):
            raise TypeError("Message body must be text.")
        try:
            normalized_account_id = str(UUID(account_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(
                "An assigned account is required to send a message."
            ) from exc
        normalized = clean_chat_body(body)
        client_message_id = str(uuid4())
        with self._pending_send_lock:
            if self._pending_send_count >= MAX_PENDING_SENDS:
                raise ValueError(
                    "The outgoing queue is full. Wait for pending messages to send."
                )
            self._pending_send_count += 1
        self._commands.put(
            ("send", client_message_id, normalized, normalized_account_id)
        )
        self._wake.set()
        return client_message_id

    def accept_incoming(self, message_id: str, claim_token: str) -> None:
        """Mark a message accepted by the UI, then queue its server acknowledgement."""
        try:
            normalized_id = str(UUID(message_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("Message ID must be a UUID.") from exc
        if not isinstance(claim_token, str) or not 32 <= len(claim_token) <= 128:
            raise ValueError("A valid message claim token is required.")
        with self._accepted_lock:
            if normalized_id not in self._accepted_ids:
                self._accepted_ids.add(normalized_id)
                self._accepted_order.append(normalized_id)
                if len(self._accepted_order) > MAX_REMEMBERED_MESSAGE_IDS:
                    self._accepted_ids.discard(self._accepted_order.popleft())
        self._commands.put(("ack", normalized_id, claim_token, None))
        self._wake.set()

    def _emit(self, kind: str, **data: Any) -> None:
        self.events.put(WorkerEvent(kind, data))

    def _is_accepted(self, message_id: str) -> bool:
        with self._accepted_lock:
            return message_id in self._accepted_ids

    def _complete_send(self) -> None:
        with self._pending_send_lock:
            self._pending_send_count = max(0, self._pending_send_count - 1)

    @staticmethod
    def _is_terminal_client_error(exc: AgentTransportError) -> bool:
        return (
            exc.status_code is not None
            and 300 <= exc.status_code < 500
            and exc.status_code
            not in {
                408,
                429,
            }
        )

    def _run(self) -> None:
        try:
            self._run_session()
        except Exception as exc:  # noqa: BLE001 - contain unexpected failures at the worker boundary
            self._emit("error", message=f"Chat session stopped ({type(exc).__name__}).")
        finally:
            self._agent_token = ""
            self._emit("stopped")

    def _run_session(self) -> None:
        client = self._client_factory(self._server_url, self._agent_token)
        pending_sends: deque[tuple[str, str, str]] = deque()
        pending_acks: deque[tuple[str, str]] = deque()
        queued_ack_ids: set[str] = set()
        delivered_ids: OrderedDict[str, None] = OrderedDict()
        account_id: str | None = None
        next_assignment_refresh = 0.0
        next_poll = 0.0
        failures = 0
        self._emit("connected")

        while not self._stop.is_set():
            while True:
                try:
                    kind, item_id, value, target_account_id = (
                        self._commands.get_nowait()
                    )
                except queue.Empty:
                    break
                if kind == "send":
                    if target_account_id is None:
                        self._complete_send()
                        self._emit("send_canceled", message_id=item_id)
                    else:
                        pending_sends.append((item_id, target_account_id, value))
                elif item_id not in queued_ack_ids:
                    pending_acks.append((item_id, value))
                    queued_ack_ids.add(item_id)

            now = time.monotonic()
            try:
                if now >= next_assignment_refresh:
                    session = client.chat_session()
                    assigned = session.get("account_id")
                    account_id = str(assigned) if assigned is not None else None
                    self._emit(
                        "assignment",
                        account_id=account_id,
                        account_nickname=session.get("account_nickname"),
                        subscription_tier=session.get("subscription_tier", "unknown"),
                        subscription_source=session.get(
                            "subscription_source", "baseline_not_recorded"
                        ),
                        subscription_evidence_source=session.get(
                            "subscription_evidence_source", "unknown"
                        ),
                        subscription_expires_at=session.get("subscription_expires_at"),
                    )
                    next_assignment_refresh = now + self._assignment_refresh_interval

                if pending_acks:
                    message_id, claim_token = pending_acks.popleft()
                    queued_ack_ids.discard(message_id)
                    try:
                        client.acknowledge_chat_message(message_id, claim_token)
                    except AgentTransportError as exc:
                        if exc.status_code == 401:
                            self._emit("auth_required")
                            return
                        if exc.status_code == 409:
                            self._emit("ack_expired", message_id=message_id)
                        elif self._is_terminal_client_error(exc):
                            self._emit(
                                "ack_failed",
                                message_id=message_id,
                                status_code=exc.status_code,
                            )
                        else:
                            pending_acks.appendleft((message_id, claim_token))
                            queued_ack_ids.add(message_id)
                            raise
                    else:
                        self._emit("acknowledged", message_id=message_id)

                if pending_sends:
                    message_id, target_account_id, body = pending_sends.popleft()
                    if target_account_id != account_id:
                        self._complete_send()
                        self._emit("send_canceled", message_id=message_id)
                    elif account_id is not None:
                        try:
                            result = client.submit_chat_message(
                                target_account_id, message_id, body
                            )
                        except AgentTransportError as exc:
                            if exc.status_code == 401:
                                self._emit("auth_required")
                                return
                            if self._is_terminal_client_error(exc):
                                self._complete_send()
                                self._emit(
                                    "send_failed",
                                    message_id=message_id,
                                    status_code=exc.status_code,
                                )
                            else:
                                pending_sends.appendleft(
                                    (message_id, target_account_id, body)
                                )
                                raise
                        else:
                            self._complete_send()
                            self._emit(
                                "sent",
                                message_id=message_id,
                                server_message_id=result["id"],
                            )

                now = time.monotonic()
                if account_id is not None and now >= next_poll:
                    message = client.poll_chat_message()
                    if message is not None:
                        message_id = str(message["id"])
                        if self._is_accepted(message_id):
                            try:
                                client.acknowledge_chat_message(
                                    message_id, message["claim_token"]
                                )
                            except AgentTransportError as exc:
                                if exc.status_code == 401:
                                    self._emit("auth_required")
                                    return
                                if exc.status_code == 409:
                                    self._emit("ack_expired", message_id=message_id)
                                elif self._is_terminal_client_error(exc):
                                    self._emit(
                                        "ack_failed",
                                        message_id=message_id,
                                        status_code=exc.status_code,
                                    )
                                else:
                                    pending_acks.appendleft(
                                        (message_id, message["claim_token"])
                                    )
                                    queued_ack_ids.add(message_id)
                                    raise
                            else:
                                self._emit("acknowledged", message_id=message_id)
                        elif message_id not in delivered_ids:
                            delivered_ids[message_id] = None
                            if len(delivered_ids) > MAX_REMEMBERED_MESSAGE_IDS:
                                delivered_ids.popitem(last=False)
                            self._emit("incoming", message=message)
                    next_poll = time.monotonic() + self._poll_interval

                failures = 0
                deadlines = [next_assignment_refresh]
                if account_id is not None:
                    deadlines.append(next_poll)
                delay = max(0.05, min(deadlines) - time.monotonic())
            except AgentTransportError as exc:
                if exc.status_code == 401:
                    self._emit("auth_required")
                    return
                if self._is_terminal_client_error(exc):
                    diagnostic = (
                        f"; Cloudflare Ray ID: {exc.cf_ray_id}" if exc.cf_ray_id else ""
                    )
                    self._emit(
                        "error",
                        message=(
                            f"Control server rejected the request "
                            f"(HTTP {exc.status_code}{diagnostic})."
                        ),
                        status_code=exc.status_code,
                        cf_ray_id=exc.cf_ray_id,
                    )
                    return
                failures += 1
                delay = min(
                    self._retry_max, self._retry_base * (2 ** min(failures - 1, 10))
                )
                self._emit("connection_error", message=str(exc), retry_seconds=delay)
            self._wake.wait(delay)
            self._wake.clear()
