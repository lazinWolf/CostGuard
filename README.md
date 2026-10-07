# CostGuard

CostGuard is a single-user, local-first prototype for pre-deployment economic regression testing of AI workloads. Run or import paired workload evidence, compare cost and declared task quality, investigate with an analyst model, and explicitly approve an experiment to measure an alternative. Deterministic accounting and policies remain authoritative; the analyst supplies interpretations and proposals.

For the intended product direction beyond this prototype, see [VISION.md](VISION.md).

## Quick demo

Requires Docker Compose. All published ports bind to localhost. Before adding real provider keys, copy `.env.example` to `.env` and replace its passwords and gateway secrets.

```bash
COSTGUARD_DEMO=1 docker compose --profile demo up -d --build --wait
```

Open [CostGuard](http://localhost:8000) and click **Create demo comparison**, or use **Workbench** to run the steps yourself. The ticket-triage fixture has ten cases, including four validation cases. The longer prompt increases observed mock cost while keeping its declared task checks unchanged. In Workbench, also try the **Cheap incorrect prompt**: it reduces cost but fails quality. Reports show paired outputs, evaluator checks, configuration changes, cost components, and the policy result.

Click **Investigate this comparison**, start the investigation, review or edit its proposed prompt, then **Run proposed experiment**. Compare the measured outcome against both the original baseline and the previous candidate. Fixture investigations always use the free scripted analyst and do not overwrite or inherit a configured real analyst. Starting runs/probes/investigations on real models is explicit and can incur charges.

The mock provider uses deterministic responses and word-based synthetic usage, with explicitly synthetic prices. This verifies the workflow, not real intelligence, actual billing, or broad task quality. No paid model or downloaded Ollama model is needed. Bifrost is available at [localhost:8080](http://localhost:8080). Its setup token is `costguard-local-setup-change-me` unless changed in `.env`. Finish Bifrost's admin setup before adding real keys; its dashboard/API are unprotected until an admin account exists. Demo setup is only available with `COSTGUARD_DEMO=1`.

The original three-case echo examples remain available for smoke checks:

```bash
docker compose --profile demo exec -T app python -m costguard.bootstrap_gateway
docker compose --profile demo exec -T app python -m costguard run examples/experiment_baseline.json --wait
docker compose --profile demo exec -T app python -m costguard run examples/experiment_candidate.json --wait
```

Start the stack again later without `--build`; PostgreSQL and Bifrost data live in named volumes. A complete walkthrough is in [docs/PROTOTYPE.md](docs/PROTOTYPE.md).

## Using your own model

Start `--profile gateway` for cloud or other OpenAI-compatible providers, or `--profile local` to also start Ollama. Configure provider URL, API key, and models in Bifrost's UI, then select its `provider/model` pair in Workbench. Supply real prices; synthetic fixture prices must not be used as actual billing estimates. Changing the model clears the browser's pricing fields. Connections offers an explicit, recorded one-call chat probe; it does not prove tool-calling support. For a provider on your private network, Bifrost requires an explicit private-network allowance in its provider settings. CostGuard never stores provider keys in experiment artifacts.

Workbench clones the baseline's inputs, expectations, and validation partitions into the candidate. Prompts, models, output limits, and temperature may change. Both executions are compared on one explicit pricing snapshot. The case-manifest fingerprint establishes identical declared test inputs, not the authenticity of an imported trace.

The browser/API is at `localhost:8000`, Bifrost at `localhost:8080`, and API docs at `localhost:8000/docs`. The stack has an app, a sequential job runner, PostgreSQL, and Bifrost; Ollama and the mock provider are profile-specific. The app runs its database migration on startup.

## Configure a real analyst

1. Configure a local or cloud model in Bifrost with chat tool-calling support.
2. On **Connections**, enter its `provider/model` ID, explicit input/output prices, a pricing version, and investigation limits. These settings are separate from the workload model and are snapshotted for each investigation. Provider credentials remain in Bifrost. If inference requires a Bifrost virtual key, set `BIFROST_VIRTUAL_KEY` in `.env` and recreate the app and runner.
3. On **Investigations**, select a comparison and ask a question. The runner executes a bounded tool loop: inspect comparison, inspect cases, optionally propose one prompt experiment, and return findings with references to inspected evidence.
4. Review or edit the proposed prompt and explicitly run it. CostGuard preserves the source case suite, workload model, pricing and execution limits. It queues the approved proposal once, then compares its measured result to the original baseline/policy and to the previous candidate. You can start another investigation on the outcome.

The analyst receives report/configuration data and exploration-case evidence requested through its tools. Validation inputs/outputs are withheld from the analyst, but available to you and executed by the workload model. Findings are model interpretations; validated references do not prove every claim is correct. Analyst spend is recorded separately, with no automatic retry or replay after a lost lease. Thresholds are checked between calls, not guaranteed caps on in-flight charges. Missing usage or a reported analyst-model mismatch stops the loop with unknown cost. Cancellation stops further calls after the current request.

Investigations, settings and measured outcomes are persisted in PostgreSQL. The API exposes `/api/v1/analyst` and `/api/v1/investigations`; MCP can list/read investigations without initiating paid calls. Imported artifacts can be investigated, but an executable proposal requires the candidate's original CostGuard job specification.

## Evidence and decisions

- Version 2 artifacts include the full case manifest, fingerprint, captured outputs, reported model, finish status, evaluator checks, usage provenance, latency, steps and optional non-model charges. Import through Workbench, `POST /api/v1/artifacts`, or `python -m costguard import <file>`. Legacy v1 imports remain readable, but missing manifest identity cannot prove a policy pass.
- `costguard.hook.ArtifactRecorder` is an opt-in Python hook, not automatic SDK interception. Provide `suite_cases` to produce comparable v2 evidence. [examples/record_application.py](examples/record_application.py) instruments a small application independently of CostGuard's job runner.
- Experiments support prompt/model changes and a bounded word-count tool example. Workload receipts are checkpointed before/after calls; partial evidence survives an expired lease. Unknown usage stops further calls. Ambiguous failures are not retried, including legacy specs with `max_attempts > 1`.
- Reports compare matched cases on one supplied pricing catalog, show model/case deltas, additive input/output/non-model cost attribution, an overlapping retry subset, mean and p95 cost/latency, behavior, and monthly projection. Missing usage, incomplete suites, changed inputs, reported-model mismatches, and failed/truncated cases prevent a verified comparison. Gateway-internal routing/retries are not captured; prices reflect the requested model only when its reported identity is compatible.
- Built-in quality checks are versioned exact-text matching or a JSON object with exactly the expected fields/values. Full compatible paired coverage is required for a quality gate; validation-partition quality is also checked when present. These checks and a small sample are not a general semantic evaluation or statistical confidence claim.
- Download a JSON evidence bundle (including both artifacts and comparison settings) or a Markdown decision summary from a report. Bundles contain workload inputs/outputs: review sensitive content before sharing.
- Policies can gate cost per request, regression percentage, projected monthly cost, latency, quality, retries, steps, and model usage. A hypothetical repricing scenario holds observed execution behavior fixed; it is not a prediction of model quality or routing behavior.
- `python -m costguard gate --baseline <artifact.json> --candidate <artifact.json> --pricing <pricing.json> --policy <policy.json>` exits 0 for pass, 1 for fail, and 2 for inconclusive/error. `--json-output` and `--markdown-output` export reports for CI.
- [examples/workload-gate.yaml](examples/workload-gate.yaml) is a template for gating changes in another application's CI. That application must produce paired artifacts first. The gate itself needs no database, gateway, or paid calls.
- `python -m costguard.mcp_server` exposes read-only MCP queries for saved workload economics and comparisons. It does not initiate paid executions.

The original stateless `POST /compare` endpoint remains available, including `examples/comparison.json`.

## Verify

```bash
docker compose --profile demo run --rm --no-deps app python -m unittest discover -v
docker compose --profile demo exec -T app python -m tests.e2e
```

GitHub Actions runs both checks against the demo stack. Tests use generated cases and deterministic assertions, not an external test-generation service.

## Prototype boundaries

This is a single-user, local-first prototype. It does not automatically capture production traces, model routing probabilities, weighted traffic, RAG behavior, or quality beyond explicit case evaluators. Each run is one observation per case; repeat experiments manually to examine variability, and do not treat measured latency as a controlled benchmark. Reported model aliases/version names that differ from the pricing identity are conservatively inconclusive. There is no guaranteed cap on an in-flight charge. Gateway administration/credentials stay in Bifrost. Workload inputs/outputs are saved locally; redact sensitive data before capture. Do not expose the stack publicly without authentication, TLS, and secret management. Real local/cloud model reasoning and outcomes must be verified with your chosen model; the automated demo checks use mock inference only.
