"""Agent versions declare fallback models and provider-specific model options (ADR 0004).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adding a column with a constant default rewrites no rows, so the append-only
    # trigger on agent_versions is not involved.
    op.add_column(
        "agent_versions",
        sa.Column("fallback_models", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "agent_versions",
        sa.Column("model_options", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "model_options")
    op.drop_column("agent_versions", "fallback_models")
