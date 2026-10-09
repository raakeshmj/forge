"""Skills, learning cycles, pinned skills on agent versions, pinned versions on tasks (ADR 0009).

Skill version content is immutable: a trigger rejects any change except status,
validation results, and the optimistic row version.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("capability", sa.Text, nullable=False),
        sa.Column("instructions", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("provenance", JSONB, nullable=False),
        sa.Column("parent_version", sa.Integer, nullable=True),
        sa.Column("validation", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "status IN ('CANDIDATE', 'VALIDATED', 'REJECTED', 'PROMOTED', 'DEPRECATED')",
            name="ck_status_valid",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION forge_skill_content_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR NEW.name <> OLD.name OR NEW.version <> OLD.version
               OR NEW.capability <> OLD.capability OR NEW.instructions <> OLD.instructions
               OR NEW.provenance <> OLD.provenance
               OR NEW.parent_version IS DISTINCT FROM OLD.parent_version
               OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'skill version content is append-only'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER skill_versions_content_immutable BEFORE UPDATE OR DELETE ON skill_versions "
        "FOR EACH ROW EXECUTE FUNCTION forge_skill_content_immutable()"
    )
    op.create_table(
        "learning_cycles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("capability", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("analysis", JSONB, nullable=False),
        sa.Column("gap", JSONB, nullable=False),
        sa.Column("skill_name", sa.Text, nullable=True),
        sa.Column("skill_version", sa.Integer, nullable=True),
        sa.Column("dataset", sa.Text, nullable=True),
        sa.Column("baseline_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("baseline_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("delta", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer, nullable=False),
    )
    op.create_index("ix_learning_cycles_status", "learning_cycles", ["status"])
    op.add_column(
        "agent_versions",
        sa.Column("skills", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "agent_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_versions.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "agent_version_id")
    op.drop_column("agent_versions", "skills")
    op.drop_table("learning_cycles")
    op.drop_table("skill_versions")
    op.execute("DROP FUNCTION forge_skill_content_immutable()")
