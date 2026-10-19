"""Emergency Stop mechanism for ZChat V6.0 (Phase 22).

Provides authoritative, thread-safe emergency stop controls covering individual
features, accounts, agents, machines, or the entire ZChat operation.

Safety guarantees
-----------------
- MUST NOT call os.kill() or subprocess on any Camfrog client PID.
- MUST NOT modify the Windows registry.
- Stop and deactivation records persist locally across process restarts.
- All public operations are thread-safe.
- Works on Linux and Windows (no Windows-only module-level imports).
- Deactivation is always explicit; stops do not auto-expire.
- Activating GLOBAL scope logically stops all lower scopes.
- EmergencyStopGuard raises StopIteration (never SystemExit) when stopped.
- In-flight operations that cannot be interrupted are reported, not killed.
- Emergency Stop cannot undo an already-executed external action.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Scope enumeration
# ---------------------------------------------------------------------------

class EmergencyStopScope(str, Enum):
    """Hierarchical scopes for emergency stop coverage.

    Hierarchy (broadest → narrowest):
        GLOBAL > MACHINE > AGENT > ACCOUNT > FEATURE
    Activating a broader scope implicitly stops all narrower scopes.
    """
    GLOBAL = "GLOBAL"
    MACHINE = "MACHINE"
    AGENT = "AGENT"
    ACCOUNT = "ACCOUNT"
    FEATURE = "FEATURE"


# Ordered from broadest to narrowest — used for hierarchy checks.
_SCOPE_HIERARCHY: List[EmergencyStopScope] = [
    EmergencyStopScope.GLOBAL,
    EmergencyStopScope.MACHINE,
    EmergencyStopScope.AGENT,
    EmergencyStopScope.ACCOUNT,
    EmergencyStopScope.FEATURE,
]


def _scope_rank(scope: EmergencyStopScope) -> int:
    """Return the rank of *scope* (lower rank = broader scope)."""
    return _SCOPE_HIERARCHY.index(scope)


# ---------------------------------------------------------------------------
# Stop reason record
# ---------------------------------------------------------------------------

@dataclass
class EmergencyStopReason:
    """Immutable record describing a single emergency stop activation.

    Attributes
    ----------
    stop_id:
        Unique identifier for this stop event.
    scope:
        The scope covered by this stop.
    reason_code:
        Machine-readable reason string (e.g., ``"OPERATOR_HALT"``).
    operator_id:
        Identity of the operator or system that activated the stop.
    timestamp:
        UNIX epoch timestamp (seconds) when the stop was activated.
    affected_resources:
        Optional list of specific resource keys affected (empty = all within scope).
    active:
        Snapshot of whether the stop was active when this record was returned or read.
        Query the registry again for current state; callers should not mutate this field.
    deactivated_at:
        UNIX epoch timestamp when the stop was explicitly deactivated, or ``None``.
    """
    scope: EmergencyStopScope
    reason_code: str
    operator_id: str
    timestamp: float = field(default_factory=time.time)
    affected_resources: List[str] = field(default_factory=list)
    stop_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    active: bool = field(default=True, init=False)
    deactivated_at: Optional[float] = field(default=None, init=False)

    def as_dict(self) -> Dict[str, Any]:
        """Return a serialisable representation suitable for audit logs."""
        return {
            "stop_id": self.stop_id,
            "scope": self.scope.value,
            "reason_code": self.reason_code,
            "operator_id": self.operator_id,
            "timestamp": self.timestamp,
            "affected_resources": list(self.affected_resources),
            "active": self.active,
            "deactivated_at": self.deactivated_at,
        }


# ---------------------------------------------------------------------------
# Registry — process singleton backed by local SQLite
# ---------------------------------------------------------------------------

class EmergencyStopRegistry:
    """Singleton view of a persistent, process-shared local stop registry.

    Thread Safety
    -------------
    SQLite serializes mutations across independent ZChat processes, while the
    Python lock serializes threads. The store is local to one OS user profile;
    it is not a multi-machine lease authority.

    Scope Hierarchy
    ---------------
    ``is_stopped`` returns ``True`` for scope *S* and optional *resource_key*
    if **any** active stop has a scope whose rank is ≤ the rank of *S*
    (i.e. a broader or equal scope is active).  A GLOBAL stop therefore causes
    every finer-grained ``is_stopped`` check to return ``True``.

    Audit Preservation
    ------------------
    Stop events remain in the local SQLite audit table after deactivation.
    They are not tamper-proof against local administrators or disk loss.
    """

    _instance: Optional["EmergencyStopRegistry"] = None
    _singleton_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "EmergencyStopRegistry":
        with cls._singleton_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._lock = threading.RLock()
                cls._instance = obj
            return cls._instance

    @staticmethod
    def _storage_path() -> Path:
        """Return the per-user store path, with an explicit test override."""
        override = os.environ.get("ZCHAT_EMERGENCY_STOP_DB")
        if override:
            return Path(override).expanduser()
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        else:
            root = Path(
                os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
            )
        return root / "ZChat" / "emergency-stops.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        path = self._storage_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
        connection = sqlite3.connect(path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emergency_stops (
                stop_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL CHECK (scope IN ('GLOBAL', 'MACHINE', 'AGENT', 'ACCOUNT', 'FEATURE')),
                reason_code TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                affected_resources TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                deactivated_at REAL
            )
            """
        )
        if os.name != "nt":
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return connection

    @staticmethod
    def _reason_from_row(row: sqlite3.Row) -> EmergencyStopReason:
        reason = EmergencyStopReason(
            scope=EmergencyStopScope(row["scope"]),
            reason_code=row["reason_code"],
            operator_id=row["operator_id"],
            timestamp=float(row["timestamp"]),
            affected_resources=json.loads(row["affected_resources"]),
            stop_id=row["stop_id"],
        )
        reason.active = bool(row["active"])
        reason.deactivated_at = row["deactivated_at"]
        return reason

    def _load_active_stops(self) -> Dict[str, EmergencyStopReason]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM emergency_stops WHERE active = 1 ORDER BY rowid"
            ).fetchall()
            return {row["stop_id"]: self._reason_from_row(row) for row in rows}
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _matching_active_stops(
        self,
        scope: EmergencyStopScope,
        resource_key: Optional[str],
    ) -> List[EmergencyStopReason]:
        """Return active stops that cover *scope* + *resource_key*."""
        target_rank = _scope_rank(scope)
        results: List[EmergencyStopReason] = []
        for stop in self._load_active_stops().values():
            stop_rank = _scope_rank(stop.scope)
            if stop_rank > target_rank:
                # This stop is narrower than the requested scope; skip.
                continue
            if stop_rank < target_rank:
                # This stop is broader — it covers everything; include.
                results.append(stop)
                continue
            # Same scope level.
            if not stop.affected_resources or resource_key is None:
                # Affects all resources at this scope.
                results.append(stop)
            elif resource_key in stop.affected_resources:
                results.append(stop)
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def activate(
        self,
        scope: EmergencyStopScope,
        reason_code: str,
        operator_id: str,
        affected_resources: Optional[List[str]] = None,
    ) -> EmergencyStopReason:
        """Activate an emergency stop for *scope*.

        Parameters
        ----------
        scope:
            The :class:`EmergencyStopScope` to cover.
        reason_code:
            Machine-readable code identifying the reason (e.g. ``"OPERATOR_HALT"``).
        operator_id:
            Identity of the entity triggering the stop.
        affected_resources:
            Optional list of specific resource keys within *scope* to affect.
            When empty or ``None``, the stop applies to **all** resources within
            the scope.

        Returns
        -------
        EmergencyStopReason
            A snapshot receipt for the newly persisted stop record.
        """
        with self._lock:
            reason = EmergencyStopReason(
                scope=scope,
                reason_code=reason_code,
                operator_id=operator_id,
                affected_resources=list(affected_resources or []),
            )
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO emergency_stops
                    (stop_id, scope, reason_code, operator_id, timestamp,
                     affected_resources, active, deactivated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, NULL)
                    """,
                    (
                        reason.stop_id,
                        reason.scope.value,
                        reason.reason_code,
                        reason.operator_id,
                        reason.timestamp,
                        json.dumps(reason.affected_resources, ensure_ascii=False),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return reason

    def deactivate(
        self,
        scope: EmergencyStopScope,
        resource_key: Optional[str] = None,
    ) -> bool:
        """Explicitly deactivate active stops matching *scope* and *resource_key*.

        Deactivation marks every matching active stop as inactive and records the
        deactivation time.  The records remain in the audit log permanently.

        Parameters
        ----------
        scope:
            The scope level to deactivate.
        resource_key:
            When provided, only deactivate stops that explicitly list this key
            in ``affected_resources``.  When ``None``, deactivate all stops at
            the given *scope* level (regardless of ``affected_resources``).

        Returns
        -------
        bool
            ``True`` if at least one active stop was deactivated, ``False``
            otherwise.
        """
        now = time.time()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT stop_id, affected_resources FROM emergency_stops "
                    "WHERE scope = ? AND active = 1",
                    (scope.value,),
                ).fetchall()
                selected = [
                    row["stop_id"]
                    for row in rows
                    if resource_key is None
                    or resource_key in json.loads(row["affected_resources"])
                ]
                if selected:
                    connection.executemany(
                        "UPDATE emergency_stops SET active = 0, deactivated_at = ? "
                        "WHERE stop_id = ? AND active = 1",
                        [(now, stop_id) for stop_id in selected],
                    )
                connection.commit()
                return bool(selected)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def is_stopped(
        self,
        scope: EmergencyStopScope,
        resource_key: Optional[str] = None,
    ) -> bool:
        """Return ``True`` if any active stop covers *scope* / *resource_key*.

        A stop at a **broader** scope (lower rank in the hierarchy) implicitly
        stops every narrower scope.  For example, an active GLOBAL stop makes
        every ``is_stopped`` call return ``True`` regardless of scope or key.

        Parameters
        ----------
        scope:
            The scope to check.
        resource_key:
            Optional specific resource key within the scope.
        """
        with self._lock:
            return bool(self._matching_active_stops(scope, resource_key))

    def get_active_stops(self) -> List[EmergencyStopReason]:
        """Return a snapshot list of all currently active stop records."""
        with self._lock:
            return list(self._load_active_stops().values())

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the full, permanent audit log as a list of dicts.

        Records are never removed, even after deactivation.
        """
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT * FROM emergency_stops ORDER BY rowid"
                ).fetchall()
                return [self._reason_from_row(row).as_dict() for row in rows]
            finally:
                connection.close()

    # ------------------------------------------------------------------
    # Test / maintenance helpers
    # ------------------------------------------------------------------

    def _reset_for_testing(self) -> None:
        """Clear all state.  ONLY call this from test code."""
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM emergency_stops")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()


# ---------------------------------------------------------------------------
# EmergencyStopGuard — context manager
# ---------------------------------------------------------------------------

class EmergencyStopGuard:
    """Context manager that gates execution on the current stop state.

    Usage
    -----
    .. code-block:: python

        guard = EmergencyStopGuard(
            scope=EmergencyStopScope.AGENT,
            resource_key="agent-42",
            registry=registry,
        )
        with guard:
            # Code here runs only if no emergency stop is active.
            do_work()

    Raises
    ------
    StopIteration
        If the registry reports the given scope/resource is stopped.
        ``StopIteration`` is used deliberately (not ``SystemExit``) so that
        callers may catch and handle it gracefully without risking interpreter
        shutdown.

    Notes
    -----
    - The guard does **not** modify Camfrog client processes.
    - It does **not** call ``os.kill()`` or modify the Windows registry.
    - The stop check is performed at ``__enter__`` time; an in-flight operation
      that was already past the guard when a stop is activated will complete.
      Such operations are considered "in-flight and cannot be interrupted" and
      must be reported separately.
    """

    def __init__(
        self,
        scope: EmergencyStopScope,
        resource_key: Optional[str] = None,
        registry: Optional[EmergencyStopRegistry] = None,
        description: str = "",
    ) -> None:
        self.scope = scope
        self.resource_key = resource_key
        self.registry = registry or EmergencyStopRegistry()
        self.description = description

    def __enter__(self) -> "EmergencyStopGuard":
        if self.registry.is_stopped(self.scope, self.resource_key):
            active = self.registry.get_active_stops()
            detail = "; ".join(
                f"[{s.scope.value}/{s.reason_code}]" for s in active
            )
            msg = (
                f"Emergency stop active for scope={self.scope.value}"
                + (f" resource={self.resource_key!r}" if self.resource_key else "")
                + (f" ({self.description})" if self.description else "")
                + (f". Active stops: {detail}" if detail else ".")
            )
            raise StopIteration(msg)
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> bool:
        # Never suppress exceptions from the guarded block.
        return False


# ---------------------------------------------------------------------------
# EmergencyStopHook — mixin for StatusScheduler / BackgroundStatusExecutor
# ---------------------------------------------------------------------------

class EmergencyStopHook:
    """Mixin that integrates emergency stop awareness into executor/scheduler classes.

    Classes that inherit from this mixin gain:

    * ``stop_registry`` — a reference to the shared :class:`EmergencyStopRegistry`.
    * ``emergency_scope`` / ``emergency_resource_key`` — configurable scope defaults.
    * ``check_emergency_stop()`` — raises ``StopIteration`` if stopped.
    * ``stop_workers_safely()`` — signals internal stop events and joins worker
      threads without killing the Camfrog client process.
    * ``report_inflight_operations()`` — returns a snapshot of operations that
      were already executing when the stop was activated.

    Integration example
    -------------------
    .. code-block:: python

        class MyScheduler(EmergencyStopHook, StatusScheduler):
            pass

        scheduler = MyScheduler()
        scheduler.emergency_scope = EmergencyStopScope.AGENT
        scheduler.emergency_resource_key = "agent-1"

        with EmergencyStopGuard(scheduler.emergency_scope, scheduler.emergency_resource_key):
            scheduler.set_single_status("available")
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stop_registry: EmergencyStopRegistry = EmergencyStopRegistry()
        self.emergency_scope: EmergencyStopScope = EmergencyStopScope.GLOBAL
        self.emergency_resource_key: Optional[str] = None
        # Tracks in-flight operation IDs for reporting.
        self._inflight_ops: Dict[str, str] = {}
        self._inflight_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core hook methods
    # ------------------------------------------------------------------

    def check_emergency_stop(
        self,
        scope: Optional[EmergencyStopScope] = None,
        resource_key: Optional[str] = None,
    ) -> None:
        """Raise ``StopIteration`` if an active stop covers the given scope.

        Uses ``self.emergency_scope`` / ``self.emergency_resource_key`` as
        defaults when the caller does not provide explicit values.
        """
        effective_scope = scope or self.emergency_scope
        effective_key = resource_key if resource_key is not None else self.emergency_resource_key
        if self.stop_registry.is_stopped(effective_scope, effective_key):
            active = self.stop_registry.get_active_stops()
            detail = "; ".join(
                f"[{s.scope.value}/{s.reason_code}]" for s in active
            )
            raise StopIteration(
                f"Emergency stop active for {effective_scope.value}"
                + (f"/{effective_key}" if effective_key else "")
                + (f": {detail}" if detail else ".")
            )

    def register_inflight(self, op_id: str, description: str) -> None:
        """Register an operation as in-flight (already executing past the guard)."""
        with self._inflight_lock:
            self._inflight_ops[op_id] = description

    def complete_inflight(self, op_id: str) -> None:
        """Mark an in-flight operation as complete."""
        with self._inflight_lock:
            self._inflight_ops.pop(op_id, None)

    def report_inflight_operations(self) -> Dict[str, str]:
        """Return a snapshot of operations that cannot be immediately interrupted.

        Returns
        -------
        dict
            Mapping of ``op_id -> description`` for each in-flight operation.
        """
        with self._inflight_lock:
            return dict(self._inflight_ops)

    def stop_workers_safely(self, join_timeout: float = 2.0) -> None:
        """Signal any ZChat-owned worker threads to stop and wait for them.

        Safe guarantees
        ---------------
        - Does **not** call ``os.kill()`` on any PID.
        - Does **not** use ``subprocess`` to terminate any process.
        - Does **not** touch the Camfrog client in any way.
        - Signals internal ``threading.Event`` stop flags where available.
        - Joins worker threads with a bounded timeout so callers are not blocked.
        """
        # Signal stop events provided by StatusScheduler or BackgroundStatusExecutor.
        stop_event: Optional[threading.Event] = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()

        worker: Optional[threading.Thread] = getattr(self, "_worker_thread", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=join_timeout)

    # ------------------------------------------------------------------
    # Convenience guard factory
    # ------------------------------------------------------------------

    def make_guard(
        self,
        scope: Optional[EmergencyStopScope] = None,
        resource_key: Optional[str] = None,
        description: str = "",
    ) -> EmergencyStopGuard:
        """Return an :class:`EmergencyStopGuard` configured for this instance."""
        return EmergencyStopGuard(
            scope=scope or self.emergency_scope,
            resource_key=resource_key if resource_key is not None else self.emergency_resource_key,
            registry=self.stop_registry,
            description=description,
        )


def main(argv: Optional[List[str]] = None) -> int:
    """Manage the machine-local emergency-stop registry from a terminal."""
    parser = argparse.ArgumentParser(description="Manage local ZChat emergency stops.")
    commands = parser.add_subparsers(dest="command", required=True)
    choices = [scope.value for scope in EmergencyStopScope]

    activate = commands.add_parser("activate", help="Persist an emergency stop.")
    activate.add_argument("--scope", choices=choices, required=True)
    activate.add_argument("--reason", required=True)
    activate.add_argument("--operator", required=True)
    activate.add_argument("--resource", action="append", default=[])

    deactivate = commands.add_parser("deactivate", help="Explicitly deactivate a stop.")
    deactivate.add_argument("--scope", choices=choices, required=True)
    deactivate.add_argument("--resource")

    commands.add_parser("status", help="List active machine-local emergency stops.")
    args = parser.parse_args(argv)
    registry = EmergencyStopRegistry()

    if args.command == "activate":
        reason = registry.activate(
            EmergencyStopScope(args.scope),
            args.reason,
            args.operator,
            affected_resources=args.resource,
        )
        print(json.dumps({"activated": True, "stop": reason.as_dict()}, ensure_ascii=False))
        return 0
    if args.command == "deactivate":
        changed = registry.deactivate(
            EmergencyStopScope(args.scope), resource_key=args.resource
        )
        print(json.dumps({"deactivated": changed, "scope": args.scope}, ensure_ascii=False))
        return 0 if changed else 1

    active = [reason.as_dict() for reason in registry.get_active_stops()]
    print(json.dumps({"active_stops": active}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
