"""Conservative analysis of complete and incomplete execution artifacts."""

from decimal import Decimal
from math import ceil

from pydantic import Field

from .analysis import _call_cost, _compare, compare_workloads
from .execution import CaseExecution, ExecutionArtifact
from .models import (
    ComparisonPolicy,
    ComparisonReport,
    ComparisonRequest,
    MetricComparison,
    ModelCall,
    PricingCatalog,
    PolicyResult,
    Schema,
    Workload,
    WorkloadRun,
)


class ComparisonDetails(Schema):
    baseline_id: str
    candidate_id: str
    status: str
    reasons: list[str] = Field(default_factory=list)
    policy: PolicyResult
    economic: ComparisonReport | None = None
    baseline_case_count: int
    candidate_case_count: int
    missing_usage_calls: int
    estimated_usage_calls: int
    failed_cases: int
    model_calls: MetricComparison
    retries: MetricComparison
    tool_steps: MetricComparison
    agent_steps: MetricComparison
    p95_case_cost_usd: MetricComparison | None = None
    p95_latency_ms: MetricComparison | None = None
    contributors: list[str] = Field(default_factory=list)


def case_cost(case: CaseExecution, prices: PricingCatalog) -> Decimal | None:
    price_map = prices.as_map()
    total = sum((charge.amount_usd for charge in case.charges), Decimal("0"))
    for call in case.calls:
        if (
            call.input_tokens is None
            or call.output_tokens is None
            or (call.provider, call.model) not in price_map
        ):
            return None
        total += _call_cost(
            ModelCall(
                provider=call.provider,
                model=call.model,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
            ),
            price_map,
        )
    return total


def _p95(values: list[Decimal]) -> Decimal:
    return sorted(values)[ceil(len(values) * Decimal("0.95")) - 1]


def _sum_metric(baseline: ExecutionArtifact, candidate: ExecutionArtifact, getter) -> MetricComparison:
    return _compare(
        Decimal(sum(getter(case) for case in baseline.cases)) / len(baseline.cases),
        Decimal(sum(getter(case) for case in candidate.cases)) / len(candidate.cases),
        Decimal("0.0001"),
    )


BEHAVIOR_LIMITS = {
    "max_candidate_mean_latency_ms": ("mean latency", lambda case: case.latency_ms),
    "max_candidate_model_calls_per_case": ("model calls per case", lambda case: len(case.calls)),
    "max_candidate_retries_per_case": (
        "retries per case", lambda case: sum(call.attempt > 1 for call in case.calls)
    ),
    "max_candidate_tool_steps_per_case": (
        "tool steps per case", lambda case: sum(step.kind == "tool" for step in case.steps)
    ),
    "max_candidate_agent_steps_per_case": (
        "agent steps per case", lambda case: sum(step.kind == "agent" for step in case.steps)
    ),
}


def compare_artifacts(
    baseline: ExecutionArtifact,
    candidate: ExecutionArtifact,
    pricing: PricingCatalog,
    policy: ComparisonPolicy | None = None,
    monthly_requests: int | None = None,
) -> ComparisonDetails:
    reasons: list[str] = []
    if baseline.suite_id != candidate.suite_id:
        reasons.append("suite_id values differ")
    if {case.case_id for case in baseline.cases} != {case.case_id for case in candidate.cases}:
        reasons.append("case_id sets differ")

    all_cases = baseline.cases + candidate.cases
    missing = sum(call.usage_source == "missing" for case in all_cases for call in case.calls)
    estimated = sum(call.usage_source == "estimated" for case in all_cases for call in case.calls)
    failed = sum(case.status != "ok" for case in all_cases)
    if missing:
        reasons.append(f"{missing} calls have missing usage")
    if failed:
        reasons.append(f"{failed} cases did not complete")
    if any(not case.calls for case in all_cases):
        reasons.append("at least one case has no model calls")
    if estimated:
        reasons.append(f"{estimated} calls use estimated usage")
    unknown_prices = sorted({
        (call.provider, call.model)
        for case in all_cases for call in case.calls
        if (call.provider, call.model) not in pricing.as_map()
    })
    if unknown_prices:
        reasons.append("pricing is missing for " + ", ".join(f"{p}/{m}" for p, m in unknown_prices))

    baseline_costs = [case_cost(case, pricing) for case in baseline.cases]
    candidate_costs = [case_cost(case, pricing) for case in candidate.cases]
    economic = None
    p95_cost = None
    p95_latency = None
    contributors: list[str] = []
    comparable = not any(
        [baseline.suite_id != candidate.suite_id,
         {case.case_id for case in baseline.cases} != {case.case_id for case in candidate.cases},
         missing, failed, estimated, unknown_prices,
         any(not case.calls for case in all_cases)]
    )
    if comparable:
        economic_policy = policy.model_copy(
            update={field: None for field in BEHAVIOR_LIMITS}
        ) if policy is not None else None
        paired_quality = {
            case.case_id: case.evaluator for case in baseline.cases
        }
        baseline_workload = Workload(
            name=baseline.name,
            runs=[_to_workload_run(case, paired_quality.get(case.case_id), pricing) for case in baseline.cases],
        )
        candidate_workload = Workload(
            name=candidate.name,
            runs=[_to_workload_run(case, paired_quality.get(case.case_id), pricing) for case in candidate.cases],
        )
        economic = compare_workloads(ComparisonRequest(
            baseline=baseline_workload,
            candidate=candidate_workload,
            pricing=pricing,
            policy=economic_policy,
            monthly_requests=monthly_requests,
        ))
        p95_cost = _compare(_p95(baseline_costs), _p95(candidate_costs), Decimal("0.00000001"))
        p95_latency = _compare(
            _p95([case.latency_ms for case in baseline.cases]),
            _p95([case.latency_ms for case in candidate.cases]),
            Decimal("0.0001"),
        )
        contributors = [
            f"{item.case_id}: ${item.cost_usd.delta} per case"
            for item in economic.case_costs if item.cost_usd.delta > 0
        ][:5]
        contributors += [
            f"{item.provider}/{item.model}: ${item.cost_per_request_usd.delta} per request"
            for item in economic.model_costs if item.cost_per_request_usd.delta > 0
        ][:5]
        if any(case.charges for case in all_cases):
            contributors.append("Non-model charges are included in total cost but not model attribution")

    if economic is None:
        policy_result = PolicyResult(status="inconclusive", violations=[], unknowns=reasons)
        status = "inconclusive"
    else:
        violations = list(economic.policy.violations)
        unknowns = list(economic.policy.unknowns)
        if policy is not None:
            for field, (label, getter) in BEHAVIOR_LIMITS.items():
                limit = getattr(policy, field)
                if limit is None:
                    continue
                observed = sum((Decimal(getter(case)) for case in candidate.cases), Decimal("0")) / len(candidate.cases)
                if observed > limit:
                    violations.append(f"candidate {label} {observed} exceeds {limit}")
        evaluated = policy is not None and any(value is not None for value in policy.model_dump().values())
        policy_result = PolicyResult(
            status="fail" if violations else "inconclusive" if unknowns else "pass" if evaluated else "not_evaluated",
            violations=violations,
            unknowns=unknowns,
        )
        economic.policy = policy_result
        status = policy_result.status

    return ComparisonDetails(
        baseline_id=str(baseline.artifact_id),
        candidate_id=str(candidate.artifact_id),
        status=status,
        reasons=reasons,
        policy=policy_result,
        economic=economic,
        baseline_case_count=len(baseline.cases),
        candidate_case_count=len(candidate.cases),
        missing_usage_calls=missing,
        estimated_usage_calls=estimated,
        failed_cases=failed,
        model_calls=_sum_metric(baseline, candidate, lambda c: len(c.calls)),
        retries=_sum_metric(baseline, candidate, lambda c: sum(call.attempt > 1 for call in c.calls)),
        tool_steps=_sum_metric(baseline, candidate, lambda c: sum(step.kind == "tool" for step in c.steps)),
        agent_steps=_sum_metric(baseline, candidate, lambda c: sum(step.kind == "agent" for step in c.steps)),
        p95_case_cost_usd=p95_cost,
        p95_latency_ms=p95_latency,
        contributors=contributors,
    )


def _to_workload_run(case: CaseExecution, evaluator: str | None, pricing: PricingCatalog) -> WorkloadRun:
    quality = case.quality_score if case.evaluator and case.evaluator == evaluator else None
    return WorkloadRun(
        case_id=case.case_id,
        calls=[ModelCall(
            provider=call.provider,
            model=call.model,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
        ) for call in case.calls],
        latency_ms=case.latency_ms,
        quality_score=quality,
        additional_charges_usd=sum((charge.amount_usd for charge in case.charges), Decimal("0")),
    )
