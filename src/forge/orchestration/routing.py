"""Agent routing: which agent performs a task (ARCHITECTURE §8, ADR 0006, ADR 0008).

Eligibility comes from the capability graph; among eligible agents the one with
the highest task fitness wins: measured competency and reliability (optimistic
bounds for low-risk work, pessimistic for riskier work), cost, latency, and
workload. Every decision records each candidate's fitness breakdown.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from forge.domain.agent import Agent, AgentVersion
from forge.domain.capability import (
    GENERAL,
    Candidate,
    CapabilityGraph,
    Competency,
    Reliability,
    fitness,
    prior,
)
from forge.domain.task import Task


@dataclass(frozen=True, slots=True)
class AgentRouting:
    agent_id: UUID | None
    candidates: tuple[str, ...]  # best first, then rejected agents with reasons

    @property
    def explanation(self) -> str:
        return "; ".join(self.candidates) or "no agents registered"


@dataclass(frozen=True, slots=True)
class RoutingEvidence:
    graph: CapabilityGraph
    competencies: Mapping[tuple[UUID, str], Competency]
    reliability: Mapping[UUID, Reliability]
    workload: Mapping[UUID, int]
    optimistic: bool  # low-risk missions explore uncertain agents


def route_task(
    task: Task, agents: Sequence[tuple[Agent, AgentVersion]], evidence: RoutingEvidence
) -> AgentRouting:
    if task.agent_id is not None:  # assigned by the operator's plan
        by_id = {agent.id: agent for agent, _ in agents}
        named = by_id.get(task.agent_id)
        reason = "assigned by plan" if named else "assigned agent does not exist"
        label = named.name if named else str(task.agent_id)
        return AgentRouting(task.agent_id if named else None, (f"{label}: {reason}",))

    required = task.required_capabilities or (GENERAL,)
    candidates: list[Candidate] = []
    rejected: list[str] = []
    for agent, version in agents:
        missing = [
            c
            for c in task.required_capabilities
            if not evidence.graph.serves(version.capabilities, c)
        ]
        if missing:
            rejected.append(f"{agent.name}: cannot serve {missing}")
            continue
        candidates.append(
            Candidate(
                agent_id=agent.id,
                name=agent.name,
                competencies={
                    c: evidence.competencies.get((agent.id, c), prior(agent.id, c))
                    for c in required
                },
                reliability=evidence.reliability.get(agent.id, Reliability()),
                workload=evidence.workload.get(agent.id, 0),
            )
        )
    ranked = fitness(candidates, optimistic=evidence.optimistic)
    described = [f.describe() for f in ranked]
    if described:
        described[0] = "selected " + described[0]
    return AgentRouting(ranked[0].agent_id if ranked else None, (*described, *sorted(rejected)))
