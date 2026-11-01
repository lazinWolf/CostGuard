# CostGuard usage guide

How to use CostGuard day to day. For the pitch and setup details see the [README](../README.md); for a scripted five-minute demo see [DEMO.md](DEMO.md); for how it is built see [PROJECT_GUIDE.md](PROJECT_GUIDE.md) and [REFERENCE.md](REFERENCE.md).

## What it answers

You changed a prompt or a model. **Will it cost more, and is it still right?** CostGuard runs the old version (the *baseline*) and the new one (the *candidate*) on the same test cases, prices both, scores the answers, and applies your limits. The result is one verdict:

| Verdict | Meaning |
| --- | --- |
| **Pass** | every limit holds |
| **Fail** | at least one limit is broken |
| **Inconclusive** | the evidence is not good enough to decide (a case failed, usage or prices are missing, a quality limit has unscored cases). It is never a false pass |

The headline number is **cost per successful task**: the cost of a request divided by the share of answers that are right. It is what one *correct* answer costs. A cheaper prompt that gets answers wrong is not cheaper.

## 1. Start it and look around (2 minutes, no model needed)

```bash
make up              # app, runner, PostgreSQL, Bifrost gateway, Ollama
make recorded        # loads a real recorded session (or click "Load the recorded example" on the home page)
```

Open <http://localhost:8000>. The recorded check shows a "friendlier" support-triage prompt costing about 10× more per request and breaking the labels (6/8 → 0/8 correct), so the check **fails**. It also holds the analyst's explanation and three measured fixes. Replays need no model, gateway or GPU.

If port 8080 is taken, set `BIFROST_PORT` in `.env`.

## 2. Run your own check

Open **New check** (<http://localhost:8000/checks/new>). Start from a preset (*Support triage* or *Refund reply*) and edit anything.

| Section | What to set |
| --- | --- |
| **1 · Model** | A gateway model ID in `provider/model` form, such as `ollama/llama3.2:3b-instruct-q4_K_M`. The page lists what the gateway offers. Prices fill in from the model's [OpenRouter rate](#prices); edit them to use your own |
| **2 · The two versions** | The baseline and candidate system prompts. Only the prompt differs; model, cases, prices and settings are identical |
| **3 · Cases** | Messages sent with both prompts, each with an expected answer. Add as many as you need (up to 50). Choose how answers are matched (below) |
| **4 · Limits** | How much cost per successful task may rise, the quality rule, and requests per month for the monthly cost estimate |

Press **Run the check**. It calls the model twice per case and may incur provider charges (local Ollama costs nothing). Runs use temperature 0, so differences come from the prompt, not from sampling. Answers appear as they arrive and the check page opens on its own.

The checkbox *Also use this model as the analyst* is off by default: small models usually cannot call tools, so pick the analyst on **Connections** instead (section 5).

### How answers are matched

| Choice | A correct answer is |
| --- | --- |
| **Ignoring case and punctuation** (`normalized`) | the expected answer, ignoring surrounding punctuation, quotes and Markdown emphasis. Best for labels and classifications |
| **Exactly** (`exact`) | the expected answer, ignoring case |
| **By a judge model** (`llm_judge`) | an answer a separate judge model says meets your rubric. For free-text output with no single right answer |

A judge is a model's opinion; its reason is shown beside each answer, and its cost is reported apart from the workload cost. If it gives no usable verdict the answer has no score and a quality limit becomes inconclusive. Use a judge stronger than the model being checked.

## 3. Read a check

The check page runs top to bottom from the decision to the evidence:

1. **Verdict and limits table.** Each limit, the measured value, and ✓ or ✗. Counts are shown as "6/8 correct".
2. **Headline numbers.** Cost per successful task first, then cost per request, monthly cost and quality. A short hint under each number defines it.
3. **What changed.** The baseline and candidate prompts with a word-level diff.
4. **Why the cost changed.** Tokens in and out per case and which side drives the cost (for example output growing from 2 to about 230 tokens).
5. **Case by case.** Every case with both answers, most changed first, so you can see exactly which answers broke.
6. **What if prices change?** The same measured tokens at other prices. Presets for common rates. This reprices; it does not predict new behaviour.
7. **Explain and fix.** The analyst (section 5).
8. **Execution details.** Latency, calls, retries, steps and links to the raw runs. Latency is noisy on local models because the first call loads the model, so do not gate on it without a warm model.

Everything is saved. **Checks** (the home page) lists past checks, and fixes are nested under the check they fix.

## 4. Limits

The New check form offers the three you will use most. The API, CLI gate and MCP accept all twelve, as fields of the policy JSON:

| Limit | Policy field | Fails when |
| --- | --- | --- |
| Cost per successful task rise | `max_cost_per_success_increase_percent` | the candidate's cost per successful task is more than this % above baseline. Needs a score on every case |
| Cost per successful task, absolute | `max_candidate_cost_per_success_usd` | the candidate's cost per successful task is above this |
| Cost per request rise | `max_cost_increase_percent` | candidate cost per request is more than this % above baseline |
| Cost per request, absolute | `max_candidate_cost_per_request_usd` | candidate cost per request is above this |
| Monthly cost | `max_monthly_cost_usd` | projected monthly cost is above this (needs requests per month) |
| Minimum quality | `min_candidate_quality_score` | the candidate's mean score is below this (0 to 1) |
| Quality drop | `max_quality_drop` | the candidate scores more than this below baseline on the same cases (0 means no worse) |
| Latency | `max_candidate_mean_latency_ms` | mean latency is above this |
| Model calls | `max_candidate_model_calls_per_case` | calls per case are above this |
| Retries | `max_candidate_retries_per_case` | retries per case are above this |
| Tool steps | `max_candidate_tool_steps_per_case` | tool steps per case are above this |
| Agent steps | `max_candidate_agent_steps_per_case` | agent steps per case are above this |

A candidate with **no successful task at all** fails a cost-per-successful-task limit outright and shows as "no successes".

## 5. Explain and fix with the analyst

When a check fails (or whenever you are curious), the **Explain and fix** section can ask an LLM *analyst* why.

**One-time setup:** open **Connections**, choose the analyst model (it must support chat tool calls, for example `ollama/granite4.2:8b-q4_K_M`), check its prices (they fill in from OpenRouter), and save.

Then on a check press **Ask the analyst why**. It starts from the evidence CostGuard already computed, so it needs only a couple of calls. You get:

- **Findings**, each linked to cases, with a number check that flags any figure the analyst quoted that is not in the evidence.
- **Up to three proposed fixes**, each a new system prompt with a one-line hypothesis.
- **Test the fixes** runs all of them against the original baseline under the same limits. The scoreboard shows cost per successful task, cost per request, correct counts and a verdict for each, and highlights the best passing fix (lowest cost per successful task; ties go to the shorter prompt).
- Each fix has its own check page. **Ask again** on a failed fix gives the analyst another go.

The analyst proposes; the measurements decide. A fix can fail, and then it is not highlighted. Findings are model interpretations, while every cost, count and verdict comes from the deterministic engine. Its own spend is reported separately from the workload cost.

## Prices

A local model has no per-token bill, so CostGuard prices it at what the same model costs on **OpenRouter** (`examples/reference_prices.json`, versioned and dated). Cloud models are priced there too, so every comparison is like with like.

| Model | Input / output per million tokens |
| --- | --- |
| Llama 3.2 3B (`llama3.2:3b…`) | $0.05 / $0.33 |
| Granite 4.2 8B (`granite4.2:8b…`) | $0.06 / $0.15 |
| Qwen 3.8 27B (`qwen3.8-27b`) | $0.15 / $1.875 |

Choosing a model fills its rates on **New check** and **Connections**. Edit them and the report says the prices were entered by you. A model not in the table has no prefilled rate: enter your provider's price. These are reference rates read on 2026-11-01, not an invoice. To add a model, add an entry to `examples/reference_prices.json`.

Cloud models: run `docker compose --profile gateway up -d --build --wait`, open Bifrost at <http://localhost:8080>, add a provider and its key (keys stay in Bifrost), then choose `provider/model` on New check.

## 6. Use it in CI

The gate compares two stored runs and exits with a status your pipeline understands:

| Exit | Meaning |
| --- | --- |
| `0` | pass |
| `1` | fail: a limit is broken |
| `2` | inconclusive, or an error |

```bash
python -m costguard gate \
  --baseline baseline.json --candidate candidate.json \
  --pricing pricing.json --policy policy.json \
  --monthly-requests 100000 \
  --markdown-output summary.md --json-output report.json
```

`baseline.json` and `candidate.json` are execution artifacts, `pricing.json` a price catalog, and `policy.json` the limits from section 4, for example `{"max_cost_per_success_increase_percent": 20, "max_quality_drop": 0}`. See `examples/ci/` for working files. `make gate` (or `bash examples/ci/gate.sh`) runs it on the recorded runs: the verbose candidate is blocked (exit 1) and the analyst's fix passes (exit 0). GitHub Actions runs the same gate and writes the markdown to the job summary.

To produce artifacts from your own app, wrap each case with `costguard.hook.ArtifactRecorder`, or import a file with `python -m costguard import <file>`. Imported evidence can be explained and gated but not re-run.

## Other ways in

**Terminal**

```bash
python -m costguard demo --model ollama/llama3.2:3b-instruct-q4_K_M --investigate   # make demo
python -m costguard load-recorded                                                   # make recorded
python -m costguard record <check-id> <investigation-id>                            # export a session as a recorded example
python -m costguard report <check-id> --json-output report.json                    # export a saved report
python -m costguard scenario <run-id> --pricing pricing.json --monthly-requests 100000   # reprice a saved run
```

`demo` takes `--input-price` and `--output-price` to override the OpenRouter rates, and `--keep-analyst` to leave the configured analyst alone.

**API.** Interactive docs at <http://localhost:8000/docs>. Start a check with `POST /api/v1/checks`, follow it with `GET /api/v1/checks/running/{candidate_job_id}`, ask the analyst with `POST /api/v1/investigations`, then `POST /api/v1/investigations/{id}/test-fixes`.

**MCP.** `python -m costguard.mcp_server` gives an assistant such as Claude Code these tools: `list_checks`, `get_check`, `explain_check` (why a check failed, from the stored evidence, with no model call), and run, report and investigation queries. `start_check` makes paid model calls, so it is refused unless the server has `COSTGUARD_MCP_ALLOW_RUNS=1`.

**Observability.** `make observe` adds Grafana at <http://localhost:3000> with a dashboard of CostGuard's own spend (workload, judge, analyst), call durations, verdicts and traces. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to send the same signals to your own collector. It never feeds a verdict.

## Make targets

| Target | Does |
| --- | --- |
| `make up` / `make down` | start or stop the stack (data volumes are kept) |
| `make models` | pull the workload and analyst models into Ollama and register them in Bifrost |
| `make warm` | load the workload model into memory so the first run does not wait |
| `make recorded` | replay the recorded example |
| `make demo` | a check plus an analyst investigation from the terminal |
| `make observe` | the stack plus Grafana and telemetry |
| `make test` / `make e2e` / `make gate` | unit tests, mock end-to-end workflow, CI cost gate |
| `make logs` | follow the app and runner logs |

## Troubleshooting

| Problem | What to do |
| --- | --- |
| New check says the gateway is unavailable | `docker compose --profile local ps`; check `BIFROST_PORT` in `.env`; run `make models` once so models are registered |
| A live run is slow | Plug a laptop in (a GPU on battery runs several times slower) and run `make warm`. Use the recorded example in the meantime |
| The analyst stops with a timeout | Raise the timeout on **Connections**; reasoning models need room to think (output limit 4,096 by default). The page names the reason |
| The analyst "answers in prose" and never finishes | The model cannot call tools. Choose a larger one |
| A fix failed | Expected sometimes (5 of 6 passed in two live sessions). Open it and **Ask again**, or edit the prompt and run a new check |
| The verdict is inconclusive | Open the limits table: the rows marked unknown tell you what is missing (an unscored case, missing usage, or no price) |
| A quality limit is unknown with a judge | The judge gave no usable verdict on some answer; use a stronger judge model |
| Numbers differ from the recorded example | The example was captured at notional $0.10 / $0.40 rates and keeps them; live checks use the OpenRouter rates |

## Limits of the prototype

Single user and local-first: no authentication, no production-trace capture, no automatic instrumentation of SDK calls. Do not expose the stack publicly without authentication, TLS and secret management. Model-call prices are per-token only; infrastructure cost is not included.
