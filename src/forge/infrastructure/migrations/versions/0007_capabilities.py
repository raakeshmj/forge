"""Capability graph and competency estimates (ADR 0008).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("parent", sa.Text, nullable=True),
        sa.Column("prerequisites", JSONB, nullable=False, server_default=_EMPTY),
        sa.Column("related", JSONB, nullable=False, server_default=_EMPTY),
        sa.Column("required_tools", JSONB, nullable=False, server_default=_EMPTY),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "competencies",
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), primary_key=True),
        sa.Column("capability", sa.Text, primary_key=True),
        sa.Column("alpha", sa.Float, nullable=False),
        sa.Column("beta", sa.Float, nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("effective_samples", sa.Float, nullable=False),
        sa.Column("recent_trend", sa.Float, nullable=True),
        sa.Column("calibration", sa.Float, nullable=True),
        sa.Column("last_evaluated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supporting_evaluations", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("alpha > 0 AND beta > 0", name="ck_beta_parameters_positive"),
    )


def downgrade() -> None:
    op.drop_table("competencies")
    op.drop_table("capabilities")
