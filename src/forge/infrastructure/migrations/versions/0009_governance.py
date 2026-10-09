"""Policies, durable approvals, audit records, CRITICAL enablements (ADR 0010).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "('RUNNING', 'WAITING_APPROVAL', 'COMPLETED', 'FAILED', 'CANCELLED')"


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("rules", JSONB, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mission_id", UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column(
            "execution_id", UUID(as_uuid=True), sa.ForeignKey("executions.id"), nullable=False
        ),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("arguments", JSONB, nullable=False),
        sa.Column("arguments_sha256", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("affected_resources", JSONB, nullable=False),
        sa.Column("risk", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Text, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text, nullable=False),
        sa.Column("row_version", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'DENIED', 'EXPIRED')", name="ck_status_valid"
        ),
    )
    op.create_index("ix_approvals_execution", "approvals", ["execution_id", "requested_at"])
    op.create_index(
        "ix_approvals_pending",
        "approvals",
        ["expires_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_table(
        "audit_records",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("actor_type", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("mission_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tool", sa.Text, nullable=True),
        sa.Column("target", sa.Text, nullable=True),
        sa.Column("approval_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("arguments_sha256", sa.Text, nullable=True),
        sa.Column("details", JSONB, nullable=False),
    )
    op.create_index("ix_audit_mission", "audit_records", ["mission_id", "occurred_at"])
    op.create_table(
        "critical_enablements",
        sa.Column("tool", sa.Text, primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("changed_by", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table in ("policies", "audit_records"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forge_reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION forge_reject_mutation()"
        )
    op.drop_constraint("ck_status_valid", "executions", type_="check")
    op.create_check_constraint("ck_status_valid", "executions", f"status IN {_STATUSES}")


def downgrade() -> None:
    op.drop_constraint("ck_status_valid", "executions", type_="check")
    op.create_check_constraint(
        "ck_status_valid", "executions", "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')"
    )
    for table in ("critical_enablements", "audit_records", "approvals", "policies"):
        op.drop_table(table)
