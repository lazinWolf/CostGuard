# CostGuard

CostGuard is a local prototype for pre-deployment economic regression testing of AI workloads. It runs matched baseline and candidate cases through an LLM gateway, saves the resulting evidence, compares cost/latency/quality and execution behavior, and applies an explicit cost policy. Token counts are an input to the analysis, not the product.

For the intended product direction beyond this prototype, see [VISION.md](VISION.md).

## Quick demo

Requires Docker Compose. All published ports bind to localhost. Before adding real provider keys, copy `.env.example` to `.env` and replace its passwords and gateway secrets.

```bash
docker compose --profile demo up -d --build --wait
docker compose --profile demo exec -T app python -m costguard.bootstrap_gateway
```

Open [CostGuard](http://localhost:8000) and [Bifrost](http://localhost:8080). The demo uses a deterministic mock OpenAI-compatible provider: no paid model or downloaded Ollama model is needed. The Bifrost setup token is `costguard-local-setup-change-me` unless changed in `.env`. Finish Bifrost's admin setup before adding real keys; its dashboard/API are unprotected until an admin account exists.

To run the bundled baseline and candidate from a terminal:

```bash
docker compose --profile demo exec -T app python -m costguard run examples/experiment_baseline.json --wait
docker compose --profile demo exec -T app python -m costguard run examples/experiment_candidate.json --wait
```

Copy their artifact IDs from the output, then select them on the CostGuard **Reports** page. The candidate's longer system prompt costs more on the mock workload, so the default 10% regression policy fails. You can also edit and launch experiments on the **Experiments** page. Start the stack again later without `--build`; PostgreSQL and Bifrost data live in named volumes.

## Using your own model

Start `--profile gateway` for cloud or other OpenAI-compatible providers, or `--profile local` to also start Ollama. Configure provider URL, API key, and models in Bifrost's UI, then use its `provider/model` pair in an experiment specification. For a provider on your private network, Bifrost requires an explicit private-network allowance in its provider settings. CostGuard never stores provider keys in experiment artifacts. Pricing is supplied as a versioned catalog in the experiment or comparison request; Bifrost's routing does not silently determine comparison prices.

The browser/API is at `localhost:8000`, Bifrost at `localhost:8080`, and API docs at `localhost:8000/docs`. The stack has an app, a sequential job runner, PostgreSQL, and Bifrost; Ollama and the mock provider are profile-specific. The app runs its database migration on startup.

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
