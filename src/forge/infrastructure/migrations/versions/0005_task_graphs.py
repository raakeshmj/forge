"""Task graphs, task retries, declared capabilities, tool scope, budget reservations (ADR 0006).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_LIST = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("tasks", sa.Column("key", sa.Text, nullable=False, server_default="main"))
    op.add_column("tasks", sa.Column("kind", sa.Text, nullable=False, server_default="WORK"))
    op.add_column(
        "tasks", sa.Column("dependencies", JSONB, nullable=False, server_default=_EMPTY_LIST)
    )
    op.add_column(
        "tasks",
        sa.Column("required_capabilities", JSONB, nullable=False, server_default=_EMPTY_LIST),
    )
    op.add_column(
        "tasks", sa.Column("max_attempts", sa.Integer, nullable=False, server_default="1")
    )
    op.add_column("tasks", sa.Column("attempts", sa.Integer, nullable=False, server_default="0"))
    op.alter_column("tasks", "agent_id", nullable=True)
    op.create_unique_constraint("uq_tasks_mission_key", "tasks", ["mission_id", "key"])
    op.create_check_constraint("ck_kind_valid", "tasks", "kind IN ('WORK', 'PLANNING')")
    op.create_check_constraint(
        "ck_routed_before_ready",
        "tasks",
        "agent_id IS NOT NULL OR status IN ('PENDING', 'FAILED', 'CANCELLED')",
    )
    op.drop_index("ix_tasks_ready", "tasks")
    op.create_index(
        "ix_tasks_ready",
        "tasks",
        ["created_at"],
        postgresql_where=sa.text("status = 'READY' AND agent_id IS NOT NULL"),
    )

    op.add_column("missions", sa.Column("tool_scope", JSONB, nullable=True))
    op.add_column(
        "agent_versions",
        sa.Column("capabilities", JSONB, nullable=False, server_default=_EMPTY_LIST),
    )

    op.drop_constraint("executions_task_id_key", "executions", type_="unique")
    op.add_column(
        "executions", sa.Column("task_attempt", sa.Integer, nullable=False, server_default="1")
    )
    op.create_unique_constraint(
        "uq_executions_task_attempt", "executions", ["task_id", "task_attempt"]
    )

    op.create_table(
        "budget_reservations",
        sa.Column(
            "execution_id", UUID(as_uuid=True), sa.ForeignKey("executions.id"), primary_key=True
        ),
        sa.Column("mission_id", UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("amount_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_micro_usd >= 0", name="ck_amount_non_negative"),
    )
    op.create_index("ix_budget_reservations_mission", "budget_reservations", ["mission_id"])


def downgrade() -> None:
    op.drop_table("budget_reservations")
    op.drop_constraint("uq_executions_task_attempt", "executions", type_="unique")
    op.drop_column("executions", "task_attempt")
    op.create_unique_constraint("executions_task_id_key", "executions", ["task_id"])
    op.drop_column("agent_versions", "capabilities")
    op.drop_column("missions", "tool_scope")
    op.drop_index("ix_tasks_ready", "tasks")
    op.create_index(
        "ix_tasks_ready", "tasks", ["created_at"], postgresql_where=sa.text("status = 'READY'")
    )
    op.drop_constraint("ck_routed_before_ready", "tasks", type_="check")
    op.drop_constraint("ck_kind_valid", "tasks", type_="check")
    op.drop_constraint("uq_tasks_mission_key", "tasks", type_="unique")
    op.alter_column("tasks", "agent_id", nullable=False)
    for column in (
        "attempts",
        "max_attempts",
        "required_capabilities",
        "dependencies",
        "kind",
        "key",
    ):
        op.drop_column("tasks", column)
