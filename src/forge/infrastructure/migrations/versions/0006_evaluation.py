"""Evaluations, dimension results, benchmarks, and benchmark runs (ADR 0007).

Evaluations, their results, and benchmark versions are append-only. At most one
automatic evaluation exists per mission, so concurrent evaluation workers cannot
duplicate one.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = ("evaluations", "evaluation_results", "benchmarks")


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mission_id", UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("evaluator_set", sa.Text, nullable=False),
        sa.Column("benchmark_case", sa.Text, nullable=True),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("trigger IN ('auto', 'manual')", name="ck_trigger_valid"),
    )
    op.create_index(
        "uq_evaluations_auto_per_mission",
        "evaluations",
        ["mission_id"],
        unique=True,
        postgresql_where=sa.text("trigger = 'auto'"),
    )
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "evaluation_id", UUID(as_uuid=True), sa.ForeignKey("evaluations.id"), nullable=False
        ),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("execution_id", UUID(as_uuid=True), nullable=True),
        sa.Column("dimension", sa.Text, nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("evaluator", sa.Text, nullable=False),
        sa.Column("findings", JSONB, nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.CheckConstraint("scope IN ('task', 'mission')", name="ck_scope_valid"),
    )
    op.create_index("ix_evaluation_results_evaluation", "evaluation_results", ["evaluation_id"])
    op.create_index(
        "ix_evaluation_results_agent_dimension", "evaluation_results", ["agent_id", "dimension"]
    )
    op.create_table(
        "benchmarks",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("capability", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("cases", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "benchmark_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("benchmark", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["benchmark", "version"], ["benchmarks.name", "benchmarks.version"]
        ),
        sa.CheckConstraint("status IN ('RUNNING', 'COMPLETED')", name="ck_status_valid"),
    )
    op.create_table(
        "benchmark_run_items",
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("benchmark_runs.id"), primary_key=True
        ),
        sa.Column("case_key", sa.Text, primary_key=True),
        sa.Column(
            "mission_id",
            UUID(as_uuid=True),
            sa.ForeignKey("missions.id"),
            nullable=False,
            unique=True,
        ),
    )
    for table in APPEND_ONLY:
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
        "benchmark_run_items",
        "benchmark_runs",
        "benchmarks",
        "evaluation_results",
        "evaluations",
    ):
        op.drop_table(table)
