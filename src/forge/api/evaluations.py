"""Evaluation and benchmark endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from forge.api.dependencies import clock, uow_factory
from forge.domain.evaluation import Benchmark, BenchmarkRun, Evaluation
from forge.evaluation import benchmarks
from forge.evaluation.service import evaluate_mission

router = APIRouter(tags=["evaluation"])


class ResultView(BaseModel):
    scope: str
    task_id: UUID | None
    agent_id: UUID | None
    execution_id: UUID | None
    dimension: str
    score: float | None
    confidence: float
    evaluator: str
    findings: list[dict[str, Any]]
    metrics: dict[str, Any]


class EvaluationView(BaseModel):
    id: UUID
    mission_id: UUID
    evaluator_set: str
    benchmark_case: str | None
    cost_micro_usd: int
    created_at: datetime
    results: list[ResultView]

    @classmethod
    def of(cls, evaluation: Evaluation) -> "EvaluationView":
        return cls(
            id=evaluation.id,
            mission_id=evaluation.mission_id,
            evaluator_set=evaluation.evaluator_set,
            benchmark_case=evaluation.benchmark_case,
            cost_micro_usd=evaluation.cost_micro_usd,
            created_at=evaluation.created_at,
            results=[
                ResultView(
                    scope=s.scope.value,
                    task_id=s.task_id,
                    agent_id=s.agent_id,
                    execution_id=s.execution_id,
                    dimension=s.result.dimension.value,
                    score=s.result.score,
                    confidence=s.result.confidence,
                    evaluator=s.result.evaluator,
                    findings=[
                        {"code": f.code, "message": f.message, "refs": list(f.refs)}
                        for f in s.result.findings
                    ],
                    metrics=dict(s.result.metrics),
                )
                for s in evaluation.results
            ],
        )


class BenchmarkView(BaseModel):
    name: str
    version: int
    capability: str
    description: str
    cases: list[dict[str, Any]]

    @classmethod
    def of(cls, benchmark: Benchmark) -> "BenchmarkView":
        return cls(
            name=benchmark.name,
            version=benchmark.version,
            capability=benchmark.capability,
            description=benchmark.description,
            cases=[
                {
                    "key": c.key,
                    "objective": c.objective,
                    "difficulty": c.difficulty,
                    "required_capabilities": list(c.required_capabilities),
                }
                for c in benchmark.cases
            ],
        )


class RunRequest(BaseModel):
    agent_id: UUID
    version: int | None = None
    budget_micro_usd: int = Field(default=100_000, gt=0)


class RunView(BaseModel):
    id: UUID
    benchmark: str
    version: int
    agent_id: UUID
    status: str
    summary: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None
    missions: dict[str, UUID]

    @classmethod
    def of(cls, run: BenchmarkRun, missions: dict[str, UUID]) -> "RunView":
        return cls(
            id=run.id,
            benchmark=run.benchmark,
            version=run.version,
            agent_id=run.agent_id,
            status=run.status.value,
            summary=dict(run.summary),
            created_at=run.created_at,
            completed_at=run.completed_at,
            missions=missions,
        )


@router.post("/missions/{mission_id}/evaluations", status_code=status.HTTP_201_CREATED)
async def evaluate(mission_id: UUID, request: Request) -> EvaluationView:
    evaluation = await evaluate_mission(
        uow_factory(request), clock(request), mission_id, trigger="manual"
    )
    if evaluation is None:  # manual evaluations are never deduplicated; defensive only
        raise RuntimeError("manual evaluation was not recorded")
    return EvaluationView.of(evaluation)


@router.get("/missions/{mission_id}/evaluations")
async def list_evaluations(mission_id: UUID, request: Request) -> list[EvaluationView]:
    async with uow_factory(request)() as uow:
        await uow.missions.get(mission_id)
        return [EvaluationView.of(e) for e in await uow.evaluations.list_for_mission(mission_id)]


@router.get("/benchmarks")
async def list_benchmarks(request: Request) -> list[BenchmarkView]:
    await benchmarks.register_builtin(uow_factory(request), clock(request))
    async with uow_factory(request)() as uow:
        return [BenchmarkView.of(b) for b in await uow.benchmarks.list_all()]


@router.post("/benchmarks/{name}/runs", status_code=status.HTTP_201_CREATED)
async def start_run(name: str, body: RunRequest, request: Request) -> RunView:
    await benchmarks.register_builtin(uow_factory(request), clock(request))
    run = await benchmarks.start_run(
        uow_factory(request),
        clock(request),
        benchmark=name,
        agent_id=body.agent_id,
        version=body.version,
        budget_micro_usd=body.budget_micro_usd,
    )
    return await _run_view(request, run.id)


@router.get("/benchmark-runs/{run_id}")
async def get_run(run_id: UUID, request: Request) -> RunView:
    return await _run_view(request, run_id)


async def _run_view(request: Request, run_id: UUID) -> RunView:
    async with uow_factory(request)() as uow:
        run = await uow.benchmarks.get_run(run_id)
        items = await uow.benchmarks.run_items(run_id)
    return RunView.of(run, {item.case_key: item.mission_id for item in items})
