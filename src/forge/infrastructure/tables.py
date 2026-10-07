"""SQLAlchemy Core table definitions mirroring the hand-written migrations.

The migrations are authoritative for schema; these definitions exist only to
build queries. tests/integration/test_schema.py fails if the two drift.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def _uuid(name: str, *, primary_key: bool = False, nullable: bool = False) -> Column[uuid.UUID]:
    return Column(name, UUID(as_uuid=True), primary_key=primary_key, nullable=nullable)


def _ts(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


agents = Table(
    "agents",
    metadata,
    _uuid("id", primary_key=True),
    Column("name", Text, nullable=False),
    Column("current_version", Integer, nullable=False),
    _ts("created_at"),
)

agent_versions = Table(
    "agent_versions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("agent_id"),
    Column("version", Integer, nullable=False),
    Column("role", Text, nullable=False),
    Column("instructions", Text, nullable=False),
    Column("model_provider", Text, nullable=False),
    Column("model_name", Text, nullable=False),
    Column("tools", JSONB, nullable=False),
    _ts("created_at"),
)

missions = Table(
    "missions",
    metadata,
    _uuid("id", primary_key=True),
    Column("version", Integer, nullable=False),
    Column("objective", Text, nullable=False),
    Column("constraints", JSONB, nullable=False),
    Column("success_criteria", JSONB, nullable=False),
    Column("required_capabilities", JSONB, nullable=False),
    Column("budget_micro_usd", BigInteger, nullable=False),
    _ts("deadline", nullable=True),
    Column("max_risk", Text, nullable=False),
    Column("execution_policy", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("correlation_id", Text, nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
)

tasks = Table(
    "tasks",
    metadata,
    _uuid("id", primary_key=True),
    Column("version", Integer, nullable=False),
    _uuid("mission_id"),
    Column("objective", Text, nullable=False),
    _uuid("agent_id"),
    Column("allowed_tools", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
)

executions = Table(
    "executions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("mission_id"),
    _uuid("task_id"),
    _uuid("agent_id"),
    _uuid("agent_version_id"),
    Column("model_provider", Text, nullable=False),
    Column("model_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("lease_owner", Text, nullable=True),
    _ts("lease_expires_at", nullable=True),
    Column("input_tokens", BigInteger, nullable=False),
    Column("output_tokens", BigInteger, nullable=False),
    Column("cache_read_input_tokens", BigInteger, nullable=False),
    Column("cache_creation_input_tokens", BigInteger, nullable=False),
    Column("cost_micro_usd", BigInteger, nullable=False),
    Column("output", Text, nullable=True),
    Column("failure_kind", Text, nullable=True),
    Column("failure_detail", Text, nullable=True),
    _ts("started_at"),
    _ts("completed_at", nullable=True),
)

checkpoints = Table(
    "checkpoints",
    metadata,
    _uuid("execution_id", primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("state_version", Integer, nullable=False),
    _ts("created_at"),
)

tool_invocations = Table(
    "tool_invocations",
    metadata,
    Column("idempotency_key", Text, primary_key=True),
    _uuid("execution_id"),
    Column("turn", Integer, nullable=False),
    Column("tool_use_id", Text, nullable=False),
    Column("tool", Text, nullable=False),
    Column("tool_version", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("invocations", Integer, nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
)

events = Table(
    "events",
    metadata,
    _uuid("id", primary_key=True),
    Column("event_type", Text, nullable=False),
    Column("aggregate_type", Text, nullable=False),
    _uuid("aggregate_id"),
    Column("sequence", Integer, nullable=False),
    _uuid("mission_id", nullable=True),
    Column("payload", JSONB, nullable=False),
    Column("correlation_id", Text, nullable=True),
    _uuid("causation_id", nullable=True),
    _ts("occurred_at"),
)
