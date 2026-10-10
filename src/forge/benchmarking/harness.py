"""The benchmark harness: every arm on the production code paths (ADR 0013).

Arms run in order on one database, so measured competency and learning carry forward
as they would in production. Each arm's missions are benchmark runs, scored against the
same versioned ground truth by the ordinary evaluation service.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

from forge.benchmarking.baselines import (
    CrashingModel,
    EffectCountingTool,
    InjectedCrash,
    NaiveContextModel,
)
from forge.benchmarking.metrics import ArmResult, MissionSample, duplicate_rate, summarize
from forge.capabilities import engine as capability_engine
from forge.domain.evaluation import Benchmark, BenchmarkCase, Dimension, Scope
from forge.domain.mission import Budget
from forge.domain.plan import PlannedTask
from forge.domain.repositories import Clock, UnitOfWorkFactory
from forge.domain.skill import CycleStatus, SkillRef
from forge.evaluation import benchmarks
from forge.evaluation.worker import evaluate_pending
from forge.inference.client import ModelClient
from forge.inference.demo import DemoInvestigationModel
from forge.inference.profiles import DEMO_MODEL, PROFILES, CachingMode
from forge.learning import cycle
from forge.learning import worker as learning_worker
from forge.learning.generation import DeterministicSkillGenerator
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, submit_mission
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.tools.base import Tool
from forge.tools.demo import MetricsReadTool

_log = logging.getLogger(__name__)

CAPABILITY = "incident-response"
BUDGET_MICRO_USD = 200_000
HARNESS_OPERATOR = "benchmark-harness"  # the actor recorded for the promotion it performs


@dataclass(frozen=True, slots=True)
class Config:
    repeats: int = 3  # runs of the whole dataset per arm
    seed: int = 7
    concurrency: int = 1  # one worker: execution order, and so cache reuse, is deterministic
    lease_seconds: float = 2.0
    crash_rate: float = 0.2  # chaos arm: chance of a crash at each injection point


@dataclass(frozen=True, slots=True)
class LearningOutcome:
    cycle_id: UUID
    status: str
    skill: str | None
    validated_correctness_delta: float | None


@dataclass(frozen=True, slots=True)
class Results:
    config: Config
    arms: tuple[ArmResult, ...]
    learning: LearningOutcome
    chaos_crashes: int
    notes: tuple[str, ...] = field(default=())

    def arm(self, name: str) -> ArmResult:
        return next(a for a in self.arms if a.name == name)


Submit = Callable[[BenchmarkCase, int], Awaitable[UUID]]


class Harness:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, config: Config) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._config = config
        self._arms: list[ArmResult] = []

    async def run(self) -> Results:
        await benchmarks.register_builtin(self._uow, self._clock)
        await capability_engine.register_builtin(self._uow, self._clock)
        async with self._uow() as uow:
            dataset = await uow.benchmarks.get(benchmarks.INCIDENT_INVESTIGATION.name)

        learner = await self._agent("generalist-a")
        await self._arm(
            "single_agent",
            "One generalist agent, one task per case.",
            dataset,
            subject=learner,
            submit=self._single(learner),
        )
        learning = await self._learn(learner)
        await self._arm(
            "after_learning",
            "The same agent after a validated, promoted skill (ADR 0009).",
            dataset,
            subject=learner,
            submit=self._single(learner),
        )

        peer = await self._agent("generalist-b")
        pool = (peer, learner)
        await self._measure(dataset, pool)  # routing evidence; not an arm
        await self._arm(
            "naive_multi_agent",
            "Gather -> conclude per case; agents assigned round-robin, ignoring ability.",
            dataset,
            subject=learner,
            submit=self._graph(dataset, pool),
        )
        await self._arm(
            "forge_routing",
            "Gather -> conclude per case; agents chosen by measured fitness (ADR 0008).",
            dataset,
            subject=learner,
            submit=self._graph(dataset, None),
        )
        await self._arm(
            "naive_context",
            "forge_routing with blind-concatenation context layout (dynamic header first).",
            dataset,
            subject=learner,
            submit=self._graph(dataset, None),
            model=NaiveContextModel(DemoInvestigationModel()),
        )
        await self._arm(
            "no_caching",
            "forge_routing with provider prompt caching disabled.",
            dataset,
            subject=learner,
            submit=self._graph(dataset, None),
            caching=False,
        )
        crashes = 0
        for offset, (name, description, side_effect) in enumerate(
            (
                (
                    "chaos",
                    "forge_routing with seeded worker crashes before model calls and around tool "
                    "calls; metrics.read keeps its real READ_ONLY classification.",
                    False,
                ),
                (
                    "chaos_side_effects",
                    "chaos, with metrics.read declared NON_IDEMPOTENT and its effects counted "
                    "outside the database.",
                    True,
                ),
            )
        ):
            rng = random.Random(self._config.seed + offset)  # noqa: S311 - reproducible chaos
            model = CrashingModel(DemoInvestigationModel(), rng, self._config.crash_rate)
            tool = EffectCountingTool(
                MetricsReadTool(), rng, self._config.crash_rate, side_effect=side_effect
            )
            await self._arm(
                name,
                description,
                dataset,
                subject=learner,
                submit=self._graph(dataset, None),
                model=model,
                tool=tool,
                duplicates=tool if side_effect else None,
            )
            crashes += model.crashes + tool.crashes
        return Results(
            config=self._config,
            arms=tuple(self._arms),
            learning=learning,
            chaos_crashes=crashes,
        )

    # --- Arms ------------------------------------------------------------------------

    async def _arm(
        self,
        name: str,
        description: str,
        dataset: Benchmark,
        *,
        subject: UUID,
        submit: Submit,
        model: ModelClient | None = None,
        caching: bool = True,
        tool: Tool | None = None,
        duplicates: EffectCountingTool | None = None,
    ) -> ArmResult:
        _log.info("benchmark arm starting", extra={"arm": name})
        missions: list[UUID] = []
        for repeat in range(self._config.repeats):
            run = {case.key: await submit(case, repeat) for case in dataset.cases}
            await benchmarks.record_run(
                self._uow, self._clock, dataset, agent_id=subject, missions=run
            )
            missions.extend(run.values())
        await self._drain(self._runtime(name, model, caching, tool), missions)
        result = summarize(
            name,
            description,
            [await self._sample(m) for m in missions],
            duplicate_rate=None if duplicates is None else duplicate_rate(dict(duplicates.effects)),
        )
        self._arms.append(result)
        return result

    def _single(self, agent_id: UUID) -> Submit:
        async def submit(case: BenchmarkCase, repeat: int) -> UUID:
            mission = await submit_mission(
                self._uow,
                self._clock,
                MissionRequest(
                    objective=case.objective,
                    agent_id=agent_id,
                    budget=Budget(BUDGET_MICRO_USD),
                    required_capabilities=case.required_capabilities,
                ),
                correlation_id=f"bench:{case.key}:{repeat}",
            )
            return mission.id

        return submit

    def _graph(self, dataset: Benchmark, pool: Sequence[UUID] | None) -> Submit:
        """Gather -> conclude. `pool` given: round-robin assignment, rotating the starting
        agent per mission so each agent concludes equally often; None: FORGE routing."""
        submitted = 0

        def agent_for(task: int) -> UUID | None:
            return None if pool is None else pool[(submitted + task) % len(pool)]

        async def submit(case: BenchmarkCase, repeat: int) -> UUID:
            nonlocal submitted
            mission = await submit_mission(
                self._uow,
                self._clock,
                MissionRequest(
                    objective=case.objective,
                    budget=Budget(BUDGET_MICRO_USD),
                    required_capabilities=case.required_capabilities,
                    tasks=(
                        PlannedTask(
                            "gather",
                            f"Gather the metrics evidence. {case.objective}",
                            required_capabilities=(CAPABILITY,),
                            agent_id=agent_for(0),
                        ),
                        PlannedTask(
                            "conclude",
                            case.objective,
                            depends_on=("gather",),
                            required_capabilities=(CAPABILITY,),
                            agent_id=agent_for(1),
                        ),
                    ),
                ),
                correlation_id=f"bench:{case.key}:{repeat}",
            )
            submitted += 1
            return mission.id

        return submit

    # --- Setup phases -----------------------------------------------------------------

    async def _agent(self, name: str) -> UUID:
        version = await register_agent(
            self._uow,
            self._clock,
            name=name,
            role="incident investigator",
            instructions="Investigate production incidents with the tools available.",
            model=DEMO_MODEL,
            tools=["metrics.read"],
            capabilities=[CAPABILITY],
            correlation_id=None,
        )
        return version.agent_id

    async def _measure(self, dataset: Benchmark, agents: Sequence[UUID]) -> None:
        """Benchmark each agent so routing has evidence; excluded from arm metrics."""
        missions: list[UUID] = []
        for agent in agents:
            for _ in range(self._config.repeats):
                run = await benchmarks.start_run(
                    self._uow, self._clock, benchmark=dataset.name, agent_id=agent
                )
                async with self._uow() as uow:
                    missions += [i.mission_id for i in await uow.benchmarks.run_items(run.id)]
        await self._drain(self._runtime("measurement", None, True, None), missions)

    async def _learn(self, agent_id: UUID) -> LearningOutcome:
        """The Phase 7 pipeline, unchanged: analyse, generate, validate offline, promote."""
        generator = DeterministicSkillGenerator()
        started = await cycle.start(
            self._uow,
            self._clock,
            agent_id,
            CAPABILITY,
            generator=generator,
            known_tools={"metrics.read"},
        )
        if started.status is CycleStatus.VALIDATING:  # its offline A/B missions must run
            await self._drain(self._runtime("learning", None, True, None), None)
            await learning_worker.step(
                self._uow, self._clock, generator=generator, auto_detect=False
            )
        async with self._uow() as uow:
            current = await uow.learning.get(started.id)
        if current.status is CycleStatus.VALIDATED:
            current = await cycle.promote(
                self._uow, self._clock, current.id, operator=HARNESS_OPERATOR
            )
        target = current.delta.get("target_correctness")
        return LearningOutcome(
            cycle_id=current.id,
            status=current.status.value,
            skill=_skill_name(current.skill),
            validated_correctness_delta=target if isinstance(target, float) else None,
        )

    # --- Execution ------------------------------------------------------------------------

    def _runtime(
        self, name: str, model: ModelClient | None, caching: bool, tool: Tool | None
    ) -> Callable[[str], Runtime]:
        profiles = dict(PROFILES)
        if not caching:
            profiles[DEMO_MODEL] = replace(PROFILES[DEMO_MODEL], caching=CachingMode.NONE)
        client = model or DemoInvestigationModel()  # one per arm: the provider cache starts cold
        metrics: Tool = tool or MetricsReadTool()

        def build(worker_id: str) -> Runtime:
            return Runtime(
                uow_factory=self._uow,
                clock=self._clock,
                models={DEMO_MODEL.provider: client},
                profiles=profiles,
                tools={"metrics.read": metrics},
                worker_id=f"bench-{name}-{worker_id}",
                lease_seconds=self._config.lease_seconds,
            )

        return build

    async def _drain(
        self, runtime_for: Callable[[str], Runtime], missions: list[UUID] | None
    ) -> None:
        """Run workers until the missions (or, if None, all work) are terminal and evaluated.

        A worker killed by an injected crash is replaced, as a supervisor would."""
        stop = asyncio.Event()
        generation = 0

        async def supervised(slot: int) -> None:
            nonlocal generation
            while not stop.is_set():
                generation += 1
                worker = Worker(runtime_for(f"{slot}-{generation}"), poll_interval_seconds=0.02)
                try:
                    await worker.run_until(stop)
                except BaseExceptionGroup as group:
                    if not group.subgroup(InjectedCrash):
                        raise

        workers = [asyncio.create_task(supervised(i)) for i in range(self._config.concurrency)]
        try:
            for _ in range(60_000):
                await evaluate_pending(self._uow, self._clock)
                if await self._settled(missions):
                    break
                await asyncio.sleep(0.05)
            else:
                raise TimeoutError("benchmark missions did not settle")
        finally:
            stop.set()
            for task in workers:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        while await evaluate_pending(self._uow, self._clock):
            pass

    async def _settled(self, missions: list[UUID] | None) -> bool:
        async with self._uow() as uow:
            if missions is None:
                return not await uow.tasks.workload() and not (
                    await uow.evaluations.missions_awaiting_evaluation(1)
                )
            for mission_id in missions:
                mission = await uow.missions.get(mission_id)
                if not mission.status.is_terminal:
                    return False
                if not await uow.evaluations.list_for_mission(mission_id):
                    return False
        return True

    async def _sample(self, mission_id: UUID) -> MissionSample:
        async with self._uow() as uow:
            mission = await uow.missions.get(mission_id)
            executions = await uow.executions.list_for_mission(mission_id)
            events = await uow.events.list_for_mission(mission_id)
            evaluation = (await uow.evaluations.list_for_mission(mission_id))[-1]
            item = await uow.benchmarks.item_for_mission(mission_id)
        scores = {
            s.result.dimension: s.result for s in evaluation.results if s.scope is Scope.MISSION
        }
        correctness = scores.get(Dimension.CORRECTNESS)
        calibration = scores.get(Dimension.CALIBRATION)
        stated = None if calibration is None else calibration.metrics.get("stated_confidence")
        evidence = scores.get(Dimension.EVIDENCE_QUALITY)
        calls = [e for e in events if e.event_type == "model.called"]
        return MissionSample(
            mission_id=mission_id,
            case=item.case_key if item else "",
            status=mission.status.value,
            passed=(
                correctness is not None
                and correctness.score is not None
                and correctness.score >= benchmarks.PASS_THRESHOLD
            ),
            correctness=None if correctness is None else correctness.score,
            evidence_quality=None if evidence is None else evidence.score,
            stated_confidence=stated if isinstance(stated, float) else None,
            tool_calls=sum(e.event_type == "tool.called" for e in events),
            latency_ms=(mission.updated_at - mission.created_at).total_seconds() * 1000,
            model_latencies_ms=tuple(float(_number(e.payload.get("latency_ms"))) for e in calls),
            input_tokens=sum(x.usage.input_tokens for x in executions),
            output_tokens=sum(x.usage.output_tokens for x in executions),
            cache_read_tokens=sum(x.usage.cache_read_input_tokens for x in executions),
            cache_write_tokens=sum(x.usage.cache_creation_input_tokens for x in executions),
            cost_micro_usd=sum(x.cost_micro_usd for x in executions),
            cache_savings_micro_usd=sum(
                int(_number(e.payload.get("cache_savings_micro_usd"))) for e in calls
            ),
            crashed=any(x.attempt > 1 for x in executions),
            failure_kinds=tuple(x.failure_kind.value for x in executions if x.failure_kind),
        )


def _skill_name(skill: SkillRef | None) -> str | None:
    return None if skill is None else f"{skill.name}@{skill.version}"


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0
