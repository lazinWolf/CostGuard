# Demonstrating CostGuard

## What the demonstration establishes

CostGuard tests an AI workload change on a fixed suite, measures its observed cost and declared task checks, applies explicit constraints, and uses an analyst to suggest an experiment. The numerical decision does not depend on the analyst agreeing with it.

## Five-minute fixture walkthrough

1. Start `COSTGUARD_DEMO=1 docker compose --profile demo up -d --build --wait`.
2. Open `http://localhost:8000/workbench`. The bundled task classifies ten support tickets into category/priority JSON, with six exploration and four validation cases.
3. Run the baseline. Inspect its captured outputs and evaluator checks, then designate it as the baseline.
4. Choose **Longer prompt**, run the candidate, and compare with a 10% cost-regression limit and quality minimum 1. The fixture fails cost while retaining its declared quality.
5. Start an investigation from the report. The free scripted analyst inspects evidence and proposes a prompt change. Review/edit it, approve its execution, and compare the outcome against both earlier variants.
6. Return to Workbench, reuse the original baseline, and choose **Cheap incorrect prompt**. This fixture costs less but fails task checks. Inspect a wrong output and the validation-partition results.
7. Download the evidence bundle or decision summary.

Fixture usage is word-based and prices are synthetic. The mock analyst is scripted. Neither establishes real-model intelligence or actual billing. Fixture investigations cannot inherit a real analyst configuration.

## Measuring a real model

Configure your provider URL/key/model in Bifrost. For Ollama, start the local profile and separately provision your chosen model. In Workbench, change the gateway route and enter explicit prices/version; zero can represent marginal local inference but excludes infrastructure costs. Configure a tool-calling analyst separately on Connections. Its model may differ from the workload model.

The chat probe is optional and chargeable, runs only on an explicit click, and records its output/usage as a job. It does not establish native tool-calling support. The real demonstration has the same steps as the fixture, but outcomes are measured rather than scripted; an analyst proposal may fail or not improve the workload. Do not guarantee a particular pass or savings result in advance.

Use representative non-sensitive cases, inspect quality coverage, and repeat experiments to examine variability. Validation inputs/outputs are excluded from analyst evidence tools, but you can inspect them. The small curated suite is not a broad semantic benchmark or a statistical confidence estimate.

## Integrating an application's own calls

The included application example uses ordinary HTTP chat requests and `ArtifactRecorder`; it runs independently of the built-in job runner. From the repository root with its dependencies available:

```bash
python -m examples.record_application --variant baseline --output /tmp/baseline.json
python -m examples.record_application --variant longer --output /tmp/candidate.json
python -m costguard gate --baseline /tmp/baseline.json --candidate /tmp/candidate.json \
  --pricing examples/fixture_prices.json --policy examples/triage_policy.json \
  --json-output /tmp/gate.json --markdown-output /tmp/gate.md
```

The longer-prompt fixture deliberately regresses, so the gate exits with code 1 and writes a failing report. These defaults require the running fixture gateway. For a real model, pass `--spec /path/to/your-spec.json` with the provider/model, explicit pricing, and the same case manifest. `--gateway` selects the endpoint; `BIFROST_VIRTUAL_KEY` is read from the environment. The variant flag applies the example task's prompt, so this script is an integration example, not a generic arbitrary-application executor. It exports observed failures rather than replaying unknown charges.

Import both artifacts in Workbench. Imported application evidence can be investigated; executable proposals require an original CostGuard job spec. To test a proposed change in an external application, apply it there, record the resulting calls, and import the new artifact.

The recorder is in-memory: durable pre/post-call checkpoints belong to CostGuard's built-in runner, not to arbitrary external application processes. Your application remains responsible for its own lifecycle and durable trace storage. Built-in experiments are limited to 100 cases, with 20,000-character case inputs and 8,000-character system prompts.

For offline CI gating, supply the two artifacts, one comparison pricing catalog and policy to `python -m costguard gate`. Copy/adapt `examples/workload-gate.yaml`, pin a reviewed CostGuard commit, and have your own capture step supply the input files. Exit codes are 0 pass, 1 fail, and 2 inconclusive/error. Avoid uploading sensitive prompts/outputs into shared CI artifacts.

## Evidence rules

- The manifest fingerprint includes case IDs, inputs, expectations and partitions; workflow parameters are captured separately.
- A partial suite or unknown usage cannot establish a pass. Legacy evidence with no manifest is explicitly unverified.
- The quality policy requires compatible scores for every pair. JSON checks enforce the declared expected fields/values, not broader task correctness.
- Input, output and non-model charges reconcile to observed cost; retry spend overlaps those components.
- Gateway-internal retries/routing and provider billing adjustments are outside the captured evidence.
- Workload calls are checkpointed before/after requests. Lost leases retain partial evidence without automatic replay. Cancellation waits for any in-flight request to finish; its charge may exceed a stop threshold.
- The stack is local-only and single-user. Saved artifacts and exported bundles contain inputs/outputs; redact before capture or sharing.
