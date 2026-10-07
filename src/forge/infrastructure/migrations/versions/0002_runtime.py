"""Phase 1 runtime: agents, missions, tasks, executions, checkpoints, tool ledger, events.

Events, checkpoints, and agent versions are append-only: triggers reject UPDATE,
DELETE, and TRUNCATE (ADR 0003).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("events", "checkpoints", "agent_versions")


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _check_in(column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{column}_valid")


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("current_version", sa.Integer, nullable=False),
        _timestamp("created_at"),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("instructions", sa.Text, nullable=False),
        sa.Column("model_provider", sa.Text, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("tools", JSONB, nullable=False),
        _timestamp("created_at"),
        sa.UniqueConstraint("agent_id", "version"),
    )
    op.create_table(
        "missions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("constraints", JSONB, nullable=False),
        sa.Column("success_criteria", JSONB, nullable=False),
        sa.Column("required_capabilities", JSONB, nullable=False),
        sa.Column("budget_micro_usd", sa.BigInteger, nullable=False),
        _timestamp("deadline", nullable=True),
        sa.Column("max_risk", sa.Text, nullable=False),
        sa.Column("execution_policy", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("correlation_id", sa.Text, nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("budget_micro_usd > 0", name="ck_budget_positive"),
        _check_in("max_risk", ("LOW", "MEDIUM", "HIGH", "CRITICAL")),
        _check_in(
            "status",
            (
                "CREATED",
                "PLANNING",
                "READY",
                "RUNNING",
                "PAUSED",
                "WAITING_APPROVAL",
                "FAILED",
                "COMPLETED",
                "CANCELLED",
            ),
        ),
    )
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("mission_id", UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("allowed_tools", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _check_in(
            "status",
            (
                "PENDING",
                "READY",
                "RUNNING",
                "PAUSED",
                "BLOCKED",
                "FAILED",
                "COMPLETED",
                "CANCELLED",
            ),
        ),
    )
    op.create_index(
        "ix_tasks_ready", "tasks", ["created_at"], postgresql_where=sa.text("status = 'READY'")
    )
    op.create_table(
        "executions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mission_id", UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=False),
        # One execution per task in Phase 1: a duplicate claim cannot create a second.
        sa.Column(
            "task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False, unique=True
        ),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column(
            "agent_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_versions.id"),
            nullable=False,
        ),
        sa.Column("model_provider", sa.Text, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("fence", sa.BigInteger, nullable=False),
        sa.Column("lease_owner", sa.Text, nullable=True),
        _timestamp("lease_expires_at", nullable=True),
        sa.Column("input_tokens", sa.BigInteger, nullable=False),
        sa.Column("output_tokens", sa.BigInteger, nullable=False),
        sa.Column("cache_read_input_tokens", sa.BigInteger, nullable=False),
        sa.Column("cache_creation_input_tokens", sa.BigInteger, nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("output", sa.Text, nullable=True),
        sa.Column("failure_kind", sa.Text, nullable=True),
        sa.Column("failure_detail", sa.Text, nullable=True),
        _timestamp("started_at"),
        _timestamp("completed_at", nullable=True),
        _check_in("status", ("RUNNING", "COMPLETED", "FAILED", "CANCELLED")),
    )
    op.create_index(
        "ix_executions_running_lease",
        "executions",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_table(
        "checkpoints",
        sa.Column(
            "execution_id",
            UUID(as_uuid=True),
            sa.ForeignKey("executions.id"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer, primary_key=True),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("state_version", sa.Integer, nullable=False),
        _timestamp("created_at"),
    )
    op.create_table(
        "tool_invocations",
        sa.Column("idempotency_key", sa.Text, primary_key=True),
        sa.Column(
            "execution_id", UUID(as_uuid=True), sa.ForeignKey("executions.id"), nullable=False
        ),
        sa.Column("turn", sa.Integer, nullable=False),
        sa.Column("tool_use_id", sa.Text, nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("tool_version", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("invocations", sa.Integer, nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _check_in("status", ("INTENDED", "SUCCEEDED", "FAILED")),
    )
    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("aggregate_type", sa.Text, nullable=False),
        sa.Column("aggregate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("mission_id", UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("correlation_id", sa.Text, nullable=True),
        sa.Column("causation_id", UUID(as_uuid=True), nullable=True),
        _timestamp("occurred_at"),
        sa.UniqueConstraint("aggregate_id", "sequence"),
    )
    op.create_index("ix_events_mission", "events", ["mission_id", "occurred_at"])

    op.execute(
        """
        CREATE FUNCTION forge_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
        END
        $$
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forge_reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION forge_reject_mutation()"
        )


def downgrade() -> None:
    for table in (
        "events",
        "tool_invocations",
        "checkpoints",
        "executions",
        "tasks",
        "missions",
        "agent_versions",
        "agents",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION forge_reject_mutation()")
