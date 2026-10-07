"""Tool registry: pinned tool versions and their status (ADR 0005).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_versions",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("version", sa.Text, primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_ref", sa.Text, nullable=False),
        sa.Column("definition_sha256", sa.Text, nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('native', 'mcp', 'remote')", name="ck_source_valid"),
    )
    op.create_table(
        "tools",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("pinned_version", sa.Text, nullable=False),
        sa.Column("pinned_sha256", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["name", "pinned_version"], ["tool_versions.name", "tool_versions.version"]
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'QUARANTINED')", name="ck_status_valid"),
    )
    op.execute(
        "CREATE TRIGGER tool_versions_append_only BEFORE UPDATE OR DELETE ON tool_versions "
        "FOR EACH ROW EXECUTE FUNCTION forge_reject_mutation()"
    )
    op.execute(
        "CREATE TRIGGER tool_versions_no_truncate BEFORE TRUNCATE ON tool_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION forge_reject_mutation()"
    )


def downgrade() -> None:
    op.drop_table("tools")
    op.drop_table("tool_versions")
