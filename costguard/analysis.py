from decimal import Decimal, ROUND_HALF_UP

from .models import (
    CaseCostComparison,
    ComparisonReport,
    ComparisonRequest,
    MetricComparison,
    ModelCostComparison,
    MonthlyProjection,
    PolicyResult,
    SuccessCost,
    Workload,
)

MILLION = Decimal("1000000")
MONEY_PRECISION = Decimal("0.00000001")
SUCCESS_PRECISION = Decimal("0.000000000001")  # A successful task can cost a hundredth of a cent.
METRIC_PRECISION = Decimal("0.0001")
PERCENT_PRECISION = Decimal("0.01")


def _round(value: Decimal, precision: Decimal) -> Decimal:
    return value.quantize(precision, rounding=ROUND_HALF_UP)


def _compare(
    baseline: Decimal,
    candidate: Decimal,
    precision: Decimal,
) -> MetricComparison:
    delta = candidate - baseline
    if baseline == 0:
        delta_percent = Decimal("0") if candidate == 0 else None
    else:
        delta_percent = _round((delta / baseline) * 100, PERCENT_PRECISION)

    return MetricComparison(
        baseline=_round(baseline, precision),
        candidate=_round(candidate, precision),
        delta=_round(delta, precision),
        delta_percent=delta_percent,
    )


def token_cost(input_tokens: int, output_tokens: int, input_per_million_usd: Decimal,
               output_per_million_usd: Decimal) -> Decimal:
    """What one call cost at explicit per-million-token prices."""
    return (Decimal(input_tokens) * input_per_million_usd + Decimal(output_tokens) * output_per_million_usd) / MILLION


def _call_cost(call, prices) -> Decimal:
    price = prices[call.key]
    input_cost = Decimal(call.input_tokens) * price.input_per_million_usd / MILLION
    output_cost = Decimal(call.output_tokens) * price.output_per_million_usd / MILLION
    return input_cost + output_cost


def _workload_costs(workload: Workload, prices) -> dict[str, Decimal]:
    return {
        run.case_id: run.additional_charges_usd + sum(
            (_call_cost(call, prices) for call in run.calls),
            start=Decimal("0"),
        )
        for run in workload.runs
    }


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, start=Decimal("0")) / Decimal(len(values))


def _model_costs(workload: Workload, prices) -> dict[tuple[str, str], Decimal]:
    totals: dict[tuple[str, str], Decimal] = {}
    for run in workload.runs:
        for call in run.calls:
            totals[call.key] = totals.get(call.key, Decimal("0")) + _call_cost(
                call, prices
            )
    run_count = Decimal(len(workload.runs))
    return {key: total / run_count for key, total in totals.items()}


def _paired_scores(request: ComparisonRequest) -> list[tuple[Decimal, Decimal]]:
    """(baseline, candidate) score for each case scored on both sides."""
    baseline = {run.case_id: run for run in request.baseline.runs}
    candidate = {run.case_id: run for run in request.candidate.runs}
    return [
        (baseline[case_id].quality_score, candidate[case_id].quality_score)
        for case_id in sorted(baseline)
        if baseline[case_id].quality_score is not None
        and candidate[case_id].quality_score is not None
    ]


def _paired_quality(
    request: ComparisonRequest,
) -> tuple[MetricComparison | None, int]:
    paired = _paired_scores(request)
    if not paired:
        return None, 0
    return (
        _compare(
            _mean([baseline_score for baseline_score, _ in paired]),
            _mean([candidate_score for _, candidate_score in paired]),
            METRIC_PRECISION,
        ),
        len(paired),
    )


def _per_success(cost: Decimal, mean_score: Decimal) -> Decimal | None:
    return _round(cost / mean_score, SUCCESS_PRECISION) if mean_score else None


def _success_cost(request: ComparisonRequest, baseline_cost: Decimal, candidate_cost: Decimal) -> SuccessCost | None:
    """What one correct answer costs on each side: mean cost per request over the mean quality score.

    A fractional score counts as a partial success. Retries are inside the cost; judge and analyst
    spend are not. A side with no success has no cost per success (None). The metric is absent
    unless every case is scored on both sides: a subset cannot vouch for the rest.
    """
    pairs = _paired_scores(request)
    if len(pairs) < len(request.baseline.runs):
        return None
    baseline = _per_success(baseline_cost, _mean([before for before, _ in pairs]))
    candidate = _per_success(candidate_cost, _mean([after for _, after in pairs]))
    if baseline is None or candidate is None:
        return SuccessCost(baseline=baseline, candidate=candidate, delta=None, delta_percent=None)
    return SuccessCost(baseline=baseline, candidate=candidate, delta=candidate - baseline,
                       delta_percent=_round((candidate - baseline) / baseline * 100, PERCENT_PRECISION))


def _success_limits(policy, success: SuccessCost | None, violations: list[str], unknowns: list[str]) -> None:
    if success is None:
        unknowns.append("cost per successful task needs every case scored on both sides")
        return
    if success.candidate is None:
        violations.append("the candidate has no successful tasks")
    elif (policy.max_candidate_cost_per_success_usd is not None
            and success.candidate > policy.max_candidate_cost_per_success_usd):
        violations.append(f"candidate cost per successful task ${success.candidate:f} exceeds "
                          f"${policy.max_candidate_cost_per_success_usd:f}")
    limit = policy.max_cost_per_success_increase_percent
    if limit is None or success.candidate is None:
        return
    if success.baseline is None:
        unknowns.append("the baseline has no successful tasks to compare cost per successful task with")
    elif success.baseline == 0:
        if success.candidate > 0:
            violations.append("cost per successful task increased from a zero-cost baseline")
    elif success.delta_percent > limit:
        violations.append(f"cost per successful task increase {success.delta_percent}% exceeds {limit}%")


def _policy_result(
    request: ComparisonRequest,
    baseline_cost: Decimal,
    candidate_cost: Decimal,
    quality: MetricComparison | None,
    quality_cases: int,
    success: SuccessCost | None,
) -> PolicyResult:
    policy = request.policy
    if policy is None or not any(value is not None for value in policy.model_dump().values()):
        return PolicyResult(status="not_evaluated", violations=[])

    violations: list[str] = []
    if policy.max_cost_increase_percent is not None:
        if baseline_cost == 0 and candidate_cost > 0:
            violations.append("cost increased from a zero-cost baseline")
        elif baseline_cost != 0:
            increase_percent = (
                (candidate_cost - baseline_cost) / baseline_cost
            ) * 100
            if increase_percent > policy.max_cost_increase_percent:
                displayed_increase = _round(increase_percent, PERCENT_PRECISION)
                violations.append(
                    f"cost increase {displayed_increase}% exceeds "
                    f"{policy.max_cost_increase_percent}%"
                )

    if (
        policy.max_candidate_cost_per_request_usd is not None
        and candidate_cost > policy.max_candidate_cost_per_request_usd
    ):
        displayed_cost = _round(candidate_cost, MONEY_PRECISION)
        violations.append(
            f"candidate cost ${displayed_cost} exceeds "
            f"${policy.max_candidate_cost_per_request_usd} per request"
        )

    unknowns: list[str] = []
    if (policy.max_cost_per_success_increase_percent is not None
            or policy.max_candidate_cost_per_success_usd is not None):
        _success_limits(policy, success, violations, unknowns)
    if any(getattr(policy, field) is not None for field in (
        "max_candidate_mean_latency_ms", "max_candidate_model_calls_per_case",
        "max_candidate_retries_per_case", "max_candidate_tool_steps_per_case",
        "max_candidate_agent_steps_per_case",
    )):
        unknowns.append("execution-behavior policies require versioned execution artifacts")
    if policy.max_monthly_cost_usd is not None:
        if request.monthly_requests is None:
            unknowns.append("monthly requests are required for the monthly cost policy")
        elif candidate_cost * request.monthly_requests > policy.max_monthly_cost_usd:
            violations.append("projected monthly cost exceeds the configured limit")
    if policy.min_candidate_quality_score is not None or policy.max_quality_drop is not None:
        cases = len(request.baseline.runs)
        if quality is None:
            unknowns.append("paired quality scores are required for the quality policy")
        elif quality_cases < cases:
            # A mean over a scored subset cannot vouch for the unscored cases.
            unknowns.append(f"quality scores cover only {quality_cases} of {cases} cases")
        else:
            if (policy.min_candidate_quality_score is not None
                    and quality.candidate < policy.min_candidate_quality_score):
                violations.append("candidate quality score is below the configured minimum")
            if policy.max_quality_drop is not None and -quality.delta > policy.max_quality_drop:
                violations.append(
                    f"quality fell from {quality.baseline.normalize():f} to {quality.candidate.normalize():f}, "
                    f"more than the allowed drop of {policy.max_quality_drop.normalize():f}"
                )

    return PolicyResult(
        status="fail" if violations else "inconclusive" if unknowns else "pass",
        violations=violations,
        unknowns=unknowns,
    )


def compare_workloads(request: ComparisonRequest) -> ComparisonReport:
    prices = request.pricing.as_map()
    baseline_costs = _workload_costs(request.baseline, prices)
    candidate_costs = _workload_costs(request.candidate, prices)

    baseline_mean_cost = _mean(list(baseline_costs.values()))
    candidate_mean_cost = _mean(list(candidate_costs.values()))
    cost_comparison = _compare(
        baseline_mean_cost,
        candidate_mean_cost,
        MONEY_PRECISION,
    )

    baseline_latency = _mean([run.latency_ms for run in request.baseline.runs])
    candidate_latency = _mean([run.latency_ms for run in request.candidate.runs])

    baseline_models = _model_costs(request.baseline, prices)
    candidate_models = _model_costs(request.candidate, prices)
    model_costs = [
        ModelCostComparison(
            provider=provider,
            model=model,
            cost_per_request_usd=_compare(
                baseline_models.get((provider, model), Decimal("0")),
                candidate_models.get((provider, model), Decimal("0")),
                MONEY_PRECISION,
            ),
        )
        for provider, model in sorted(set(baseline_models) | set(candidate_models))
    ]
    model_costs.sort(
        key=lambda item: (
            -item.cost_per_request_usd.delta,
            item.provider,
            item.model,
        )
    )

    case_costs = [
        CaseCostComparison(
            case_id=case_id,
            cost_usd=_compare(
                baseline_costs[case_id],
                candidate_costs[case_id],
                MONEY_PRECISION,
            ),
        )
        for case_id in baseline_costs
    ]
    case_costs.sort(key=lambda item: (-item.cost_usd.delta, item.case_id))

    monthly_projection = None
    if request.monthly_requests is not None:
        monthly_projection = MonthlyProjection(
            requests=request.monthly_requests,
            cost_usd=_compare(
                baseline_mean_cost * request.monthly_requests,
                candidate_mean_cost * request.monthly_requests,
                MONEY_PRECISION,
            ),
        )

    quality, quality_cases_compared = _paired_quality(request)
    success = _success_cost(request, baseline_mean_cost, candidate_mean_cost)
    assumptions = [
        "Every workload case has equal weight.",
        "Costs use the supplied pricing catalog and observed token counts.",
    ]
    if quality is not None:
        assumptions.append(
            "Quality compares only cases scored in both baseline and candidate."
        )
    if success is not None:
        assumptions.append(
            "Cost per successful task is mean cost per request divided by mean quality score; "
            "judge and analyst spend are excluded."
        )
    if request.monthly_requests is not None:
        assumptions.append(
            "Monthly projection assumes the compared workload mix is representative."
        )

    return ComparisonReport(
        baseline=request.baseline.name,
        candidate=request.candidate.name,
        cases_compared=len(request.baseline.runs),
        pricing_version=request.pricing.version,
        cost_per_request_usd=cost_comparison,
        mean_latency_ms=_compare(
            baseline_latency,
            candidate_latency,
            METRIC_PRECISION,
        ),
        quality_score=quality,
        quality_cases_compared=quality_cases_compared,
        cost_per_successful_task_usd=success,
        case_costs=case_costs,
        model_costs=model_costs,
        monthly_projection=monthly_projection,
        policy=_policy_result(request, baseline_mean_cost, candidate_mean_cost, quality,
                              quality_cases_compared, success),
        assumptions=assumptions,
    )
