"""Represent uncertain provider effects without claiming success or failure.

Revision ID: 0007_unknown_command_outcomes
Revises: 0006_account_nickname_claims
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_unknown_command_outcomes"
down_revision = "0006_account_nickname_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commands") as batch_op:
        batch_op.drop_constraint("ck_commands_state", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_state",
            "state IN ('pending', 'claimed', 'succeeded', 'failed', 'effect_unknown', 'canceled')",
        )
        batch_op.drop_constraint("ck_commands_result_code", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_result_code",
            "result_code IS NULL OR result_code IN "
            "('ok', 'handoff_accepted', 'provider_not_configured', 'client_not_found', "
            "'timed_out', 'execution_failed', 'lease_expired')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    uncertain = connection.execute(
        sa.text(
            "SELECT count(*) FROM commands "
            "WHERE state = 'effect_unknown' OR result_code = 'handoff_accepted'"
        )
    ).scalar_one()
    if uncertain:
        raise RuntimeError(
            "Cannot downgrade while uncertain command outcomes exist; preserve the evidence."
        )

    with op.batch_alter_table("commands") as batch_op:
        batch_op.drop_constraint("ck_commands_result_code", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_result_code",
            "result_code IS NULL OR result_code IN "
            "('ok', 'provider_not_configured', 'client_not_found', 'timed_out', "
            "'execution_failed', 'lease_expired')",
        )
        batch_op.drop_constraint("ck_commands_state", type_="check")
        batch_op.create_check_constraint(
            "ck_commands_state",
            "state IN ('pending', 'claimed', 'succeeded', 'failed', 'canceled')",
        )
