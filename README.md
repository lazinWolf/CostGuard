# CostGuard

CostGuard is a local prototype for model-led economic investigation and pre-deployment regression testing of AI workloads. An analyst model can inspect saved comparisons and cases, propose a prompt experiment, and help you measure its outcome. The economic engine compares observed cost, latency, quality and execution behavior and applies explicit policies.

For the intended product direction beyond this prototype, see [VISION.md](VISION.md).

## Quick demo

Requires Docker Compose. All published ports bind to localhost. Before adding real provider keys, copy `.env.example` to `.env` and replace its passwords and gateway secrets.

```bash
COSTGUARD_DEMO=1 docker compose --profile demo up -d --build --wait
```

Open [CostGuard](http://localhost:8000) and click **Create demo comparison**. CostGuard registers its mock provider, runs paired cases, and opens the report. Click **Investigate this comparison**, then start the investigation. Review the findings and proposal, click **Run proposed experiment**, and compare the completed result against the original baseline. If an analyst was already configured, the demo preserves it: check the displayed analyst before starting an investigation.

The mock workload and scripted mock analyst require no paid model or downloaded Ollama model. The analyst exercises the integration; it does not demonstrate real model reasoning. Bifrost is available at [localhost:8080](http://localhost:8080). Its setup token is `costguard-local-setup-change-me` unless changed in `.env`. Finish Bifrost's admin setup before adding real keys; its dashboard/API are unprotected until an admin account exists. The browser demo setup action is only available when `COSTGUARD_DEMO=1`.

To run the bundled baseline and candidate from a terminal:

```bash
docker compose --profile demo exec -T app python -m costguard.bootstrap_gateway
docker compose --profile demo exec -T app python -m costguard run examples/experiment_baseline.json --wait
docker compose --profile demo exec -T app python -m costguard run examples/experiment_candidate.json --wait
```

Copy their artifact IDs from the output, then select them on the CostGuard **Reports** page. The candidate's longer system prompt costs more on the mock workload, so the default 10% regression policy fails. You can also edit and launch experiments on the **Experiments** page. Start the stack again later without `--build`; PostgreSQL and Bifrost data live in named volumes.

## Using your own model

Start `--profile gateway` for cloud or other OpenAI-compatible providers, or `--profile local` to also start Ollama. Configure provider URL, API key, and models in Bifrost's UI, then use its `provider/model` pair in an experiment specification. For a provider on your private network, Bifrost requires an explicit private-network allowance in its provider settings. CostGuard never stores provider keys in experiment artifacts. Pricing is supplied as a versioned catalog in the experiment or comparison request; Bifrost's routing does not silently determine comparison prices.

The browser/API is at `localhost:8000`, Bifrost at `localhost:8080`, and API docs at `localhost:8000/docs`. The stack has an app, a sequential job runner, PostgreSQL, and Bifrost; Ollama and the mock provider are profile-specific. The app runs its database migration on startup.

## Configure a real analyst

1. Configure a local or cloud model in Bifrost with chat tool-calling support.
2. On **Connections**, enter its `provider/model` ID, explicit input/output prices, a pricing version, and investigation limits. These settings are separate from the workload model and are snapshotted for each investigation. Provider credentials remain in Bifrost. If inference requires a Bifrost virtual key, set `BIFROST_VIRTUAL_KEY` in `.env` and recreate the app and runner.
3. On **Investigations**, select a comparison and ask a question. The runner executes a bounded tool loop: inspect comparison, inspect cases, optionally propose one prompt experiment, and return findings with references to inspected evidence.
4. Review and explicitly run a proposal. CostGuard preserves the source case suite, workload model, pricing and execution limits. It queues the proposal once, then compares its measured result to the original baseline and policy. You can start another investigation on that result.

The analyst receives selected report/configuration data and available case inputs requested through its tools. Findings are model interpretations; validated evidence references do not prove every claim is correct. Numerical policy results remain computed by the economic engine. Model calls and analyst spend are recorded separately, with no automatic retry or replay after a lost lease. Spend thresholds are checked between calls, not guaranteed caps on in-flight charges. Missing analyst usage stops the loop with unknown cost. A cancelled investigation stops further calls after the current request.

Investigations, settings and measured outcomes are persisted in PostgreSQL. The API exposes `/api/v1/analyst` and `/api/v1/investigations`; MCP can list/read investigations without initiating paid calls. Imported artifacts can be investigated, but an executable proposal requires the candidate's original CostGuard job specification.

## Evidence and decisions

- Versioned JSON execution artifacts contain case IDs, model calls, usage provenance, latency, steps, optional quality scores, and optional non-model charges. They can be imported through `POST /api/v1/artifacts` or `python -m costguard import <file>`.
- `costguard.hook.ArtifactRecorder` is an opt-in Python hook for existing applications: wrap each case, record actual OpenAI-compatible responses plus tool/retrieval steps and optional quality, then export `recorder.artifact().model_dump_json()`. It does not intercept SDK calls automatically.
- Experiments support prompt/model changes and a bounded word-count tool example. Each run has case, time, retry, step, tool, output-token, and optional spend limits. The runner does not replay paid calls after a lost job lease.
- Reports compare matched cases on one supplied pricing catalog, attribute model and case cost changes, show mean and p95 cost/latency, model calls, retries, tool/agent steps, exact-match quality where available, and optional monthly projection. Missing usage or failed cases produce an inconclusive economic result instead of a false pass.
- Policies can gate cost per request, regression percentage, projected monthly cost, latency, quality, retries, steps, and model usage. A hypothetical repricing scenario holds observed execution behavior fixed; it is not a prediction of model quality or routing behavior.
- `python -m costguard gate --baseline <artifact.json> --candidate <artifact.json> --pricing <pricing.json> --policy <policy.json>` exits 0 for pass, 1 for fail, and 2 for inconclusive/error. `--json-output` and `--markdown-output` export reports for CI.
- `python -m costguard.mcp_server` exposes read-only MCP queries for saved workload economics and comparisons. It does not initiate paid executions.

The original stateless `POST /compare` endpoint remains available, including `examples/comparison.json`.

## Verify

```bash
docker compose --profile demo run --rm --no-deps app python -m unittest discover -v
docker compose --profile demo exec -T app python -m tests.e2e
```

GitHub Actions runs both checks against the demo stack. Tests use generated cases and deterministic assertions, not an external test-generation service.

## Prototype boundaries

This is a single-user, local-first prototype. It does not yet capture production traces, model routing probabilities, weighted traffic, automatic application instrumentation, RAG behavior, or quality beyond explicit case evaluators. It does not guarantee a spending cap for an in-flight call; the runner checks its spend threshold between cases. Gateway administration and provider credentials stay in Bifrost, while CostGuard owns the experiment evidence and economic decisions. Do not expose this stack publicly without adding authentication, TLS, and secret management.
