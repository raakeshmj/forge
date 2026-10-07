"""Benchmark datasets and runs (EVALUATION §14, ADR 0007).

A run submits one mission per case for a chosen agent; ordinary workers execute
them; the evaluation service scores each with its case's ground truth and
completes the run once every mission is evaluated.
"""

from datetime import datetime
from uuid import UUID, uuid4

from forge.domain.common import JSONValue
from forge.domain.evaluation import (
    Benchmark,
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunItem,
    BenchmarkRunStatus,
    Dimension,
    Evaluation,
    Expectations,
)
from forge.domain.mission import Budget
from forge.domain.repositories import Clock, NotFoundError, UnitOfWork, UnitOfWorkFactory
from forge.orchestration.missions import MissionRequest, submit_mission

PASS_THRESHOLD = 0.99  # correctness at or above this counts as a pass
_CALIBRATION_BINS = 5

INCIDENT_INVESTIGATION = Benchmark(
    name="incident-investigation",
    version=1,
    capability="incident-response",
    description="Locate the service and time of a latency regression from metrics.",
    cases=(
        BenchmarkCase(
            key="checkout-regression",
            objective="Investigate why checkout latency increased after yesterday's deployment.",
            required_capabilities=("incident-response",),
            difficulty=1,
            expectations=Expectations(
                must_include=("checkout", "regress"),
                expected_tools=("metrics.read",),
                reference_evidence=("880",),
                ideal_turns=2,
                reference_cost_micro_usd=3_000,
            ),
        ),
        BenchmarkCase(
            key="payments-steady",
            objective="Investigate whether payments latency changed after yesterday's deployment.",
            required_capabilities=("incident-response",),
            difficulty=2,
            expectations=Expectations(
                must_include=("payments", "no regression"),
                must_not_include=("checkout",),
                expected_tools=("metrics.read",),
                reference_evidence=("191",),
                ideal_turns=2,
                reference_cost_micro_usd=3_000,
            ),
        ),
        BenchmarkCase(
            key="inventory-spike",
            objective="Investigate the inventory latency spike this morning.",
            required_capabilities=("incident-response",),
            difficulty=2,
            expectations=Expectations(
                must_include=("inventory", "09:00"),
                must_not_include=("checkout",),
                expected_tools=("metrics.read",),
                reference_evidence=("640",),
                ideal_turns=2,
                reference_cost_micro_usd=3_000,
            ),
        ),
    ),
)

BUILTIN = (INCIDENT_INVESTIGATION,)


async def register_builtin(uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
    async with uow_factory() as uow:
        for benchmark in BUILTIN:
            await uow.benchmarks.add(benchmark, clock.now())


async def start_run(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    benchmark: str,
    agent_id: UUID,
    version: int | None = None,
    budget_micro_usd: int = 100_000,
) -> BenchmarkRun:
    """Submit one mission per case; workers execute them and the evaluator scores them."""
    async with uow_factory() as uow:
        definition = await uow.benchmarks.get(benchmark, version)
        await uow.agents.get(agent_id)
    run_id = uuid4()
    items = []
    for case in definition.cases:
        mission = await submit_mission(
            uow_factory,
            clock,
            MissionRequest(
                objective=case.objective,
                agent_id=agent_id,
                budget=Budget(budget_micro_usd),
                required_capabilities=case.required_capabilities,
            ),
            correlation_id=f"benchmark:{run_id}:{case.key}",
        )
        items.append(BenchmarkRunItem(run_id, case.key, mission.id))
    run = BenchmarkRun(
        id=run_id,
        benchmark=definition.name,
        version=definition.version,
        agent_id=agent_id,
        status=BenchmarkRunStatus.RUNNING,
        summary={},
        created_at=clock.now(),
        completed_at=None,
    )
    async with uow_factory() as uow:
        await uow.benchmarks.add_run(run, items)
    return run


async def expectations_for(uow: UnitOfWork, mission_id: UUID) -> tuple[str, Expectations] | None:
    """The ground truth for a benchmark mission: ("name@version/case", expectations)."""
    item = await uow.benchmarks.item_for_mission(mission_id)
    if item is None:
        return None
    run = await uow.benchmarks.get_run(item.run_id)
    benchmark = await uow.benchmarks.get(run.benchmark, run.version)
    return f"{benchmark.ref}/{item.case_key}", benchmark.case(item.case_key).expectations


async def settle_run_if_done(
    uow: UnitOfWork, mission_id: UUID, now: datetime
) -> BenchmarkRun | None:
    """Complete the run this mission belongs to once every case has an evaluation."""
    item = await uow.benchmarks.item_for_mission(mission_id)
    if item is None:
        return None
    run = await uow.benchmarks.get_run(item.run_id, for_update=True)
    if run.status is BenchmarkRunStatus.COMPLETED:
        return run
    evaluations: dict[str, Evaluation] = {}
    for run_item in await uow.benchmarks.run_items(run.id):
        found = await uow.evaluations.list_for_mission(run_item.mission_id)
        if not found:
            return None
        evaluations[run_item.case_key] = found[-1]
    spent = 0
    for run_item in await uow.benchmarks.run_items(run.id):
        spent += await uow.executions.mission_spend(run_item.mission_id)
    completed = BenchmarkRun(
        id=run.id,
        benchmark=run.benchmark,
        version=run.version,
        agent_id=run.agent_id,
        status=BenchmarkRunStatus.COMPLETED,
        summary=summarize(evaluations, spent),
        created_at=run.created_at,
        completed_at=now,
    )
    await uow.benchmarks.complete_run(completed)
    return completed


def summarize(evaluations: dict[str, Evaluation], spent_micro_usd: int) -> dict[str, JSONValue]:
    """Pass rate, mean per dimension, calibration error, and cost per success."""
    per_case: dict[str, JSONValue] = {}
    means: dict[str, list[float]] = {}
    passes = 0
    calibration_pairs: list[tuple[float, float]] = []
    for key, evaluation in sorted(evaluations.items()):
        scores: dict[str, JSONValue] = {}
        for dimension in Dimension:
            result = evaluation.mission_result(dimension)
            score = None if result is None else result.score
            scores[dimension.value] = score
            if score is not None:
                means.setdefault(dimension.value, []).append(score)
        correct = scores.get(Dimension.CORRECTNESS.value)
        passed = isinstance(correct, float) and correct >= PASS_THRESHOLD
        passes += passed
        calibration = evaluation.mission_result(Dimension.CALIBRATION)
        stated = None if calibration is None else calibration.metrics.get("stated_confidence")
        if isinstance(stated, float) and isinstance(correct, float):
            calibration_pairs.append((stated, 1.0 if passed else 0.0))
        per_case[key] = {"passed": passed, "scores": scores, "evaluation_id": str(evaluation.id)}
    total = len(evaluations)
    return {
        "cases": total,
        "passed": passes,
        "pass_rate": round(passes / total, 4) if total else 0.0,
        "mean_scores": {d: round(sum(v) / len(v), 4) for d, v in sorted(means.items())},
        "expected_calibration_error": expected_calibration_error(calibration_pairs),
        "cost_micro_usd": spent_micro_usd,
        "cost_per_success_micro_usd": spent_micro_usd // passes if passes else None,
        "per_case": per_case,
    }


def expected_calibration_error(pairs: list[tuple[float, float]]) -> float | None:
    """ECE over (stated confidence, outcome) pairs in equal-width bins (EVALUATION §8)."""
    if not pairs:
        return None
    bins: dict[int, list[tuple[float, float]]] = {}
    for confidence, outcome in pairs:
        bins.setdefault(min(int(confidence * _CALIBRATION_BINS), _CALIBRATION_BINS - 1), []).append(
            (confidence, outcome)
        )
    error = 0.0
    for members in bins.values():
        mean_confidence = sum(c for c, _ in members) / len(members)
        accuracy = sum(o for _, o in members) / len(members)
        error += len(members) / len(pairs) * abs(mean_confidence - accuracy)
    return round(error, 4)


async def get_or_register(
    uow: UnitOfWork, name: str, version: int | None, now: datetime
) -> Benchmark:
    try:
        return await uow.benchmarks.get(name, version)
    except NotFoundError:
        for benchmark in BUILTIN:
            if benchmark.name == name and version in (None, benchmark.version):
                await uow.benchmarks.add(benchmark, now)
                return benchmark
        raise
