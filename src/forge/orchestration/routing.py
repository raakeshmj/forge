"""Agent routing: which agent performs a task (ARCHITECTURE §8, ADR 0006).

Phase 4 routes on declared capabilities and current workload; Phase 6 replaces
the scoring with measured competency. Every decision records its alternatives.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from forge.domain.agent import Agent, AgentVersion
from forge.domain.task import Task


@dataclass(frozen=True, slots=True)
class AgentRouting:
    agent_id: UUID | None
    candidates: tuple[str, ...]  # "agent-name: reason", best first

    @property
    def explanation(self) -> str:
        return "; ".join(self.candidates) or "no agents registered"


def route_task(
    task: Task,
    agents: Sequence[tuple[Agent, AgentVersion]],
    workload: Mapping[UUID, int],
) -> AgentRouting:
    if task.agent_id is not None:  # assigned by the operator's plan
        by_id = {agent.id: agent for agent, _ in agents}
        named = by_id.get(task.agent_id)
        reason = "assigned by plan" if named else "assigned agent does not exist"
        label = named.name if named else str(task.agent_id)
        return AgentRouting(task.agent_id if named else None, (f"{label}: {reason}",))

    required = set(task.required_capabilities)
    eligible: list[tuple[int, str, Agent]] = []
    rejected: list[str] = []
    for agent, version in agents:
        missing = required - set(version.capabilities)
        if missing:
            rejected.append(f"{agent.name}: lacks {sorted(missing)}")
        else:
            eligible.append((workload.get(agent.id, 0), agent.name, agent))
    eligible.sort(key=lambda item: (item[0], item[1]))
    ranked = [f"{name}: eligible, workload {load}" for load, name, _ in eligible]
    if eligible:
        ranked[0] = ranked[0].replace("eligible", "selected", 1)
    return AgentRouting(eligible[0][2].id if eligible else None, (*ranked, *sorted(rejected)))
