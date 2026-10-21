# CostGuard — Mid-project guide

**Current milestone:** a functional, local-first prototype for pre-deployment economic regression analysis and model-led investigation of AI workloads.

## Purpose

AI application costs change with prompts, models, context, retries and execution behavior. CostGuard compares baseline and candidate workloads: **What changed, what does it cost, does it meet our constraints, and what should we test next?** Token pricing is an internal calculation—not the product.

## What we have implemented

- **Execution:** queued experiments, observed usage/latency/outputs, exact or normalized label quality checks, a bounded tool example, and versioned JSON evidence. Imports and an opt-in Python recorder support application-supplied evidence.
- **Analysis:** paired-case cost/latency, model/case contributors, execution metrics, monthly projections and policy gates. Missing usage, failed cases or incomplete quality coverage make comparisons inconclusive.
- **Intelligence:** a separate analyst inspects evidence through bounded tools, provides referenced findings and proposes prompt experiments; its spend is recorded separately. CostGuard marks any number in a finding that is absent from the evidence the analyst inspected.
- **Controlled improvement:** explicit approval executes a proposal, then measures its outcome against the original baseline and policy.
- **Real-model demo:** a support-triage suite run on any gateway model (local Ollama or cloud) from the overview page or `python -m costguard demo`.
- **What-if pricing:** each report reprices both observed runs under other per-token prices and monthly traffic, holding behavior fixed.
- **Verification:** 53 unit tests and the mock end-to-end workflow pass.
- **DevOps:** GitHub Actions builds the image, runs the unit tests, a cost gate on recorded runs (the verbose candidate is blocked, the fix passes, both shown in the job summary) and the end-to-end workflow on the Compose stack.

## Components and responsibilities

| Component | Role |
| --- | --- |
| Python / FastAPI / Jinja2 | Economic engine, API and browser UI |
| Runner container | Sequential workloads and bounded analyst investigations |
| PostgreSQL / SQLAlchemy / Alembic | Persistent evidence, jobs, reports and migrations |
| Bifrost gateway | Provider connections, credentials and model access |
| Optional Ollama / mock provider | Local models / deterministic free demonstration |
| CLI / read-only MCP | CI economic gates and agent-readable evidence |

Docker Compose connects the services; GitHub Actions runs the tests.

## Decisions taken

1. **Deterministic decisions:** LLM explanations are hypotheses, not authoritative accounting.
2. **Explicit assumptions:** both variants use one versioned pricing catalog; monthly projections require traffic estimates.
3. **Separate responsibilities:** analyst and workload models may differ; provider management and keys stay in Bifrost.
4. **Controlled execution:** approve proposals, bound activity and avoid replay after lost leases. Spend thresholds are not hard caps.
5. **Narrow scope:** local-first services and a simple UI; no forced RAG pipeline or speculative platform expansion.

The local model set is Llama 3.2 3B Instruct, Qwen 3.5 4B and Granite 4.2 8B, each using the Ollama `Q4_K_M` tag. Ollama loads one model at a time by default. Hardware and memory affect inference speed.

## Evaluation walkthrough

Open `http://localhost:8000` → choose a model → **Run baseline and candidate** → inspect the cost and quality regression with per-case outputs → **Investigate this comparison** → review the analyst's findings (number-checked, linked to cases) → run its proposal → compare the outcome against the original baseline → try other prices with **What if prices change?** → show `bash examples/ci/gate.sh` or the CI job summary blocking the candidate.

Recorded on a laptop RTX 3070 Ti (`llama3.2:3b` workload at temperature 0, `granite4.2:8b` analyst): the "helpful" candidate prompt cost +1,009% per request and dropped quality from 6/8 to 0/8. The analyst's first proposal still allowed a restatement and failed (2/8 correct). Investigating that result produced the strict wording, which passed against the original baseline at the same cost and quality. `make restore` loads this session as a demo fallback. On Groq (`qwen/qwen3.8-27b`, catalog prices) the candidate cost +2,204% and failed quality; the analyst's proposal passed against the original baseline for about $0.035 in total. These Groq runs are the recorded evidence for the CI cost gate. Local prices are notional reference rates, not provider billing. The offline mock demo (scripted analyst) remains for CI.

## Boundaries and next step

Single-user and localhost-only; not production-ready. Quality covers declared checks; a quality policy is inconclusive unless every case has paired scores. Automatic instrumentation, production tracing, RAG and behavioral simulation are not implemented; gateway-internal activity is not fully observed. Scope is frozen: focused bug fixes, clarity and demo preparation only.
