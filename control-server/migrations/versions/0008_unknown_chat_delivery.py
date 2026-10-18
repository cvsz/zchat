"""Keep uncertain internal message delivery outcomes distinguishable.

Revision ID: 0008_unknown_chat_delivery
Revises: 0007_unknown_command_outcomes
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_unknown_chat_delivery"
down_revision = "0007_unknown_command_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.drop_constraint("ck_chat_messages_state", type_="check")
        batch_op.create_check_constraint(
            "ck_chat_messages_state",
            "state IN ('queued', 'claimed', 'received', 'delivered', 'failed', 'canceled', "
            "'delivery_unknown')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    uncertain = connection.execute(
        sa.text("SELECT count(*) FROM chat_messages WHERE state = 'delivery_unknown'")
    ).scalar_one()
    if uncertain:
        raise RuntimeError(
            "Cannot downgrade while unknown chat delivery outcomes exist; preserve the evidence."
        )

    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.drop_constraint("ck_chat_messages_state", type_="check")
        batch_op.create_check_constraint(
            "ck_chat_messages_state",
            "state IN ('queued', 'claimed', 'received', 'delivered', 'failed', 'canceled')",
        )
