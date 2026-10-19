from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from camfrog_control_server.models import Account, AccountNicknameClaim, Agent, ControlCommand


def test_operator_reconciliation_migration_refuses_to_drop_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    engine = create_engine(database_url)

    try:
        alembic_command.upgrade(config, "head")
        now = datetime.now(UTC)
        agent_id, account_id, command_id = (uuid4() for _ in range(3))
        nickname_key = "f" * 64
        with Session(engine) as session:
            session.add(
                Agent(
                    id=agent_id,
                    name="reconciliation-migration-agent",
                    token_hash="e" * 64,
                    runtime_state="unknown",
                    created_at=now,
                )
            )
            session.add(AccountNicknameClaim(nickname_key=nickname_key))
            session.add(
                Account(
                    id=account_id,
                    nickname="reconciliation-migration-account",
                    nickname_key=nickname_key,
                    agent_id=agent_id,
                    enabled=True,
                    created_at=now,
                )
            )
            session.add(
                ControlCommand(
                    id=command_id,
                    agent_id=agent_id,
                    account_id=account_id,
                    operation="launch",
                    payload={},
                    state="operator_reconciled",
                    claim_attempts=1,
                    result_code="operator_reconciled",
                    created_at=now,
                    finished_at=now,
                )
            )
            session.commit()

        with pytest.raises(RuntimeError, match="operator reconciliation records"):
            alembic_command.downgrade(config, "0008_unknown_chat_delivery")

        with Session(engine) as session:
            command_record = session.get(ControlCommand, command_id)
            assert command_record is not None
            assert command_record.state == "operator_reconciled"
            revision = session.execute(text("SELECT version_num FROM alembic_version"))
            assert revision.scalar_one() == "0009_operator_reconcile"
            session.delete(command_record)
            session.commit()

        alembic_command.downgrade(config, "0008_unknown_chat_delivery")
        alembic_command.upgrade(config, "head")
    finally:
        engine.dispose()
