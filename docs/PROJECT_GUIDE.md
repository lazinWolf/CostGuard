# CostGuard — Mid-project guide

**Current milestone:** a functional, local-first prototype for pre-deployment economic regression analysis, with a model analyst that explains a failing change and has its proposed fixes measured.

## Purpose

AI application costs change with prompts, models, context, retries and execution behavior. CostGuard compares baseline and candidate workloads: **what changed, what does it cost, does it meet our constraints, and what should we test next?** Token pricing is an internal calculation, not the product.

## Vocabulary

| Word | Meaning |
| --- | --- |
| **Check** | A saved comparison of a baseline and a candidate on the same cases, with a verdict |
| **Limits** | The conditions a check must meet (cost, quality, behavior); the verdict is pass, fail or inconclusive |
| **Analyst** | A model that reads a check's evidence and proposes fixes. It cannot run anything |
| **Fix** | A prompt the analyst proposed. It is measured as a check against the original baseline |
| **Judge** | A model that scores answers against a rubric, when there is no single right label |
| **Recorded example** | A real session replayed without any model |

## What is implemented

- **Checks:** edit prompts, cases, scoring and limits in the browser (or call the API); a runner executes both versions, and the comparison is saved server-side when the second finishes. Presets: support triage (label match) and refund replies (judged).
- **Scoring:** `exact`, `normalized` and `llm_judge` evaluators in a registry. A judge's reason and cost are stored on the case, outside the workload cost; no usable verdict means no score, so a quality limit turns inconclusive.
- **Analysis:** paired-case cost and latency, model and case contributors, execution metrics, monthly projections, what-if repricing and policy gates. Missing usage, failed cases or incomplete quality coverage make a comparison inconclusive.
- **Analyst:** starts from a digest of what CostGuard already computed (limits, cost drivers, prompt change, changed cases), uses three read-only tools and proposes one to three prompts. Its spend is recorded separately, and every number in a finding is checked against its evidence.
- **Fix search:** one click runs every proposed fix; the runner compares each with the original baseline and limits, and a deterministic rule highlights the cheapest passing fix.
- **Replay:** `examples/recorded/support-triage.json` is a real session (check, analyst run, three fixes) loaded instantly by `POST /api/v1/examples/recorded`, `make recorded` or the home page. Verdicts are recomputed on load.
- **Surfaces over one service layer:** browser, HTTP API, CLI, MCP and the runner all call `costguard/service.py`. MCP adds `list_checks`, `get_check` and `explain_check`; `start_check` (paid calls) needs `COSTGUARD_MCP_ALLOW_RUNS=1`.
- **Events:** one JSON line per event through pluggable sinks (`events.add_sink`).
- **DevOps:** GitHub Actions builds the image, runs the unit tests, a cost gate on recorded runs (the verbose candidate is blocked, the fix passes, both shown in the job summary) and the end-to-end workflow on the Compose stack.
- **Verification:** 125 unit tests and the mock end-to-end workflow pass.

## Components and responsibilities

| Component | Role |
| --- | --- |
| `service.py` | The product's verbs: create and finish checks, explain, test fixes, replay. Surfaces call it |
| `economics.py`, `diagnosis.py` | Deterministic cost, policy and explanation logic (pure functions over saved evidence) |
| `evaluators.py`, `investigation.py` | Scoring registry; the analyst loop and its tool registry |
| FastAPI / Jinja2 | API and browser pages (`web.py` is thin) |
| Runner container | Sequential workloads and bounded analyst runs; finishes checks and fixes |
| PostgreSQL / SQLAlchemy / Alembic | Evidence, jobs, reports and migrations (0001–0004) |
| Bifrost gateway | Provider connections, credentials and model access |
| Optional Ollama / mock provider | Local models / deterministic demonstration for CI |
| CLI / MCP | CI economic gates and agent-readable evidence |

Docker Compose connects the services; GitHub Actions runs the tests.

## Decisions taken

1. **Deterministic decisions:** LLM explanations and judgements are evidence and hypotheses, not authoritative accounting; fixes are measured, not trusted.
2. **Explicit assumptions:** both variants use one versioned pricing catalog; monthly projections need traffic estimates.
3. **Separate responsibilities:** workload, analyst and judge models may differ; provider management and keys stay in Bifrost.
4. **Controlled execution:** the analyst cannot run anything; approving a fix test queues bounded runs. Spend thresholds are not hard caps.
5. **Seams, not platforms:** registries and one service layer make observability, MCP tools and evaluators additive, but only what is listed above is built.
6. **A demo that cannot fail on hardware:** a recorded replay is the default path; live runs are optional.

## Extension points

See "Extending it" in the README: a new evaluator is one registered function, an analyst tool one decorated handler, an observability backend one event sink, an MCP tool one function that calls the service, a starting suite one JSON file. OpenTelemetry/Prometheus export, trace import as an evidence source and analyst-drafted test cases are not built.

## Evaluation walkthrough

Open `http://localhost:8000` → **Load the recorded example** → read the verdict and limits table → the prompt diff and why the cost changed → **Explain and fix**: the analyst's findings (number-checked, linked to cases), then the scoreboard of three measured fixes and the highlighted best one → try other prices with **What if prices change?** → **New check** for a live run or the judged refund preset → show `bash examples/ci/gate.sh` or the CI job summary blocking the candidate. The step-by-step script is [DEMO.md](DEMO.md).

Recorded on a laptop RTX 3070 Ti (`llama3.2:3b` workload at temperature 0, `granite4.2:8b` analyst): the "helpful" candidate prompt cost +1,045 % per request and dropped quality from 6/8 to 0/8; the analyst proposed three prompts and all three passed against the original baseline (0 %, −11 %, −20 % cost). In another live session one of three failed at 5/8, so the fix search does not guarantee a pass. On Groq (`qwen/qwen3.8-27b`, catalog prices) the candidate cost +2,204 % and failed quality; those runs are the evidence for the CI cost gate. Local prices are notional reference rates, not provider billing.

## Boundaries and next step

Single-user and localhost-only; not production-ready. Quality covers the declared evaluators; a judge is a model's opinion and small local judges are weak. A quality limit is inconclusive unless every case has paired scores. Automatic instrumentation, production tracing, RAG and behavioral simulation are not implemented; gateway-internal activity is not fully observed. Scope is frozen: focused bug fixes, clarity and demo preparation only.
