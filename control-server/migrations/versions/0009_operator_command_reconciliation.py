"""Add an explicit, audited operator resolution for unknown command outcomes.

Revision ID: 0009_operator_reconcile
Revises: 0008_unknown_chat_delivery
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_operator_reconcile"
down_revision = "0008_unknown_chat_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commands") as batch_op:
        batch_op.drop_constraint("ck_commands_state", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_state",
            "state IN ('pending', 'claimed', 'succeeded', 'failed', 'effect_unknown', "
            "'operator_reconciled', 'canceled')",
        )
        batch_op.alter_column(
            "state", existing_type=sa.String(length=16), type_=sa.String(length=24)
        )
        batch_op.drop_constraint("ck_commands_result_code", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_result_code",
            "result_code IS NULL OR result_code IN "
            "('ok', 'handoff_accepted', 'provider_not_configured', 'client_not_found', "
            "'timed_out', 'execution_failed', 'lease_expired', 'operator_reconciled')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    reconciled = connection.execute(
        sa.text(
            "SELECT count(*) FROM commands "
            "WHERE state = 'operator_reconciled' OR result_code = 'operator_reconciled'"
        )
    ).scalar_one()
    if reconciled:
        raise RuntimeError(
            "Cannot downgrade while operator reconciliation records exist; preserve the audit."
        )

    with op.batch_alter_table("commands") as batch_op:
        batch_op.drop_constraint("ck_commands_result_code", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_result_code",
            "result_code IS NULL OR result_code IN "
            "('ok', 'handoff_accepted', 'provider_not_configured', 'client_not_found', "
            "'timed_out', 'execution_failed', 'lease_expired')",
        )
        batch_op.drop_constraint("ck_commands_state", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_state",
            "state IN ('pending', 'claimed', 'succeeded', 'failed', 'effect_unknown', 'canceled')",
        )
        batch_op.alter_column(
            "state", existing_type=sa.String(length=24), type_=sa.String(length=16)
        )
