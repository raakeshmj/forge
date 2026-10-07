from dataclasses import replace

import pytest

from forge.domain.cost import Usage
from forge.domain.evaluation import Dimension, Expectations
from forge.domain.model import ModelResponse, TextBlock
from forge.evaluation import evaluators
from forge.evaluation.benchmarks import expected_calibration_error
from forge.evaluation.judge import JudgeEvaluator
from forge.evaluation.trajectory import Subject, ToolCallRecord
from forge.inference.client import ModelCallError
from forge.inference.profiles import CachingMode, ModelProfile
from tests import factories
from tests.runtime_doubles import TEST_MODEL, TEST_PRICE, ScriptedModel

pytestmark = pytest.mark.anyio

EXPECT = Expectations(
    must_include=("checkout", "regress"),
    must_not_include=("payments",),
    expected_tools=("metrics.read",),
    reference_evidence=("880",),
    ideal_turns=2,
    reference_cost_micro_usd=500,
)


def call(
    tool: str = "metrics.read",
    status: str = "completed",
    args: str = '{"s":"checkout"}',
    result: str = "p95 14:10=880",
    stage: str | None = None,
) -> ToolCallRecord:
    return ToolCallRecord("c1", tool, f"{tool}:{args}", args, 0, status, stage, result, False)


def subject(**overrides: object) -> Subject:
    base = Subject(
        output="Checkout latency regressed to 880ms after the deploy. Confidence: 0.8",
        completed=True,
        tool_calls=(call(),),
        turns=2,
        max_steps=8,
        model_attempts_failed=0,
        usage=Usage(1000, 100, cache_read_input_tokens=1000),
        cost_micro_usd=400,
        budget_micro_usd=10_000,
        models=("test/scripted-1",),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def scores(s: Subject, expected: Expectations | None = EXPECT) -> dict[Dimension, float | None]:
    return {r.dimension: r.score for r in evaluators.evaluate(s, expected)}


def test_a_good_answer_scores_well_on_every_dimension() -> None:
    result = scores(subject())

    assert result[Dimension.CORRECTNESS] == 1.0
    assert result[Dimension.EVIDENCE_QUALITY] == 1.0
    assert result[Dimension.TRAJECTORY_QUALITY] == 1.0
    assert result[Dimension.TOOL_SELECTION] == 1.0
    assert result[Dimension.SAFETY] == 1.0
    assert result[Dimension.CALIBRATION] == pytest.approx(0.8)
    assert result[Dimension.EFFICIENCY] == pytest.approx(0.8 + 0.2 * 0.5)
    assert result[Dimension.COST_EFFICIENCY] == 1.0


def test_without_ground_truth_correctness_and_calibration_are_not_invented() -> None:
    results = {r.dimension: r for r in evaluators.evaluate(subject(), None)}

    assert results[Dimension.CORRECTNESS].score is None
    assert results[Dimension.CORRECTNESS].findings[0].code == "no_reference"
    assert results[Dimension.CALIBRATION].score is None


def test_wrong_answers_are_explained_by_findings() -> None:
    wrong = subject(output="Payments are fine. Confidence: 0.9")
    results = {r.dimension: r for r in evaluators.evaluate(wrong, EXPECT)}

    correctness = results[Dimension.CORRECTNESS]
    assert correctness.score == 0.0
    assert {f.code for f in correctness.findings} == {"missing_expected", "contains_forbidden"}
    assert results[Dimension.CALIBRATION].findings[0].code == "overconfident"
    assert results[Dimension.EVIDENCE_QUALITY].findings[0].code == "ungrounded_answer"


def test_messy_trajectories_are_penalised() -> None:
    messy = subject(
        tool_calls=(
            call(),
            call(),
            call("deploy.restart", "denied", stage="permission"),
            call("logs.read", "failed"),
        ),
        model_attempts_failed=2,
    )
    results = {r.dimension: r for r in evaluators.evaluate(messy, EXPECT)}

    trajectory = results[Dimension.TRAJECTORY_QUALITY]
    assert trajectory.score == pytest.approx(1 - 0.2 - 0.2 - 0.1 - 0.1)
    assert {f.code for f in trajectory.findings} >= {
        "duplicate_tool_call",
        "denied_call",
        "failed_call",
    }
    assert results[Dimension.SAFETY].score == 0.75
    tool_selection = results[Dimension.TOOL_SELECTION].score
    assert tool_selection is not None
    assert tool_selection < 1.0


def test_incomplete_execution_caps_trajectory_quality() -> None:
    assert scores(subject(completed=False))[Dimension.TRAJECTORY_QUALITY] == 0.2


def test_secrets_in_answers_or_arguments_are_a_safety_failure() -> None:
    leaked = subject(output="Use key sk-abcdefghijklmnopqrstu to retry")

    assert scores(leaked)[Dimension.SAFETY] == 0.0


def test_cost_efficiency_scales_quality_by_reference_cost() -> None:
    expensive = subject(cost_micro_usd=2_000)

    assert scores(expensive)[Dimension.COST_EFFICIENCY] == pytest.approx(0.25)


def test_expected_calibration_error() -> None:
    assert expected_calibration_error([]) is None
    assert expected_calibration_error([(0.9, 1.0), (0.9, 1.0)]) == pytest.approx(0.1)
    assert expected_calibration_error([(0.9, 0.0), (0.1, 1.0)]) == pytest.approx(0.9)


# --- LLM judge ---------------------------------------------------------------------------

JUDGE_PROFILE = ModelProfile(TEST_MODEL, 100_000, 4096, TEST_PRICE, True, CachingMode.NONE, 0, 3)


def judge_reply(text: str) -> ModelResponse:
    return ModelResponse((TextBlock(text),), "end_turn", Usage(500, 50))


async def test_judge_scores_from_its_json_verdict_and_is_priced() -> None:
    model = ScriptedModel(judge_reply('{"score": 0.9, "confidence": 0.8, "reasoning": "grounded"}'))
    judge = JudgeEvaluator(model, JUDGE_PROFILE)

    verdict = await judge.correctness(factories.mission(), subject(models=("other/model",)))

    assert (verdict.result.score, verdict.result.confidence) == (0.9, 0.8)
    assert verdict.cost_micro_usd == 600
    assert "<answer>" in model.requests[0].messages[0].content[0].text  # type: ignore[union-attr]


async def test_judge_on_the_agents_own_model_has_its_confidence_halved() -> None:
    judge = JudgeEvaluator(
        ScriptedModel(judge_reply('{"score": 1, "confidence": 0.8}')), JUDGE_PROFILE
    )

    verdict = await judge.correctness(factories.mission(), subject())

    assert verdict.result.confidence == 0.4
    assert "self_preference_risk" in {f.code for f in verdict.result.findings}


@pytest.mark.parametrize(
    "reply", [judge_reply("I think it's fine"), ModelCallError("down", retryable=True)]
)
async def test_judge_failures_leave_correctness_unscored(reply: object) -> None:
    judge = JudgeEvaluator(ScriptedModel(reply), JUDGE_PROFILE)  # type: ignore[arg-type]

    verdict = await judge.correctness(factories.mission(), subject())

    assert verdict.result.score is None
