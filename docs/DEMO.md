# CostGuard demo script (about 5 minutes)

The story: a prompt change that looks harmless in review makes every request ten times more expensive and breaks the output. CostGuard measures it, blocks it, has an analyst model explain it and propose fixes, measures every fix, and gates the same change in CI.

The default path is a **replay** of a real recorded session, so it cannot fail on hardware. A live variant follows for when you want to run a model on stage.

## Before you start

1. `make up`, then `make recorded` (or click **Load the recorded example** on the home page). If port 8080 is taken, set `BIFROST_PORT=8081` in `.env`.
2. Open these tabs: <http://localhost:8000>, the latest GitHub Actions run (its **Summary** page), a terminal in the repository, and `docs/costguard-architecture.svg`.
3. For the live variant only:
   - **Plug the laptop in.** On battery the GPU runs several times slower: a live check that takes 20 s on AC took 108 s, and an analyst run 171 s.
   - Free the GPU (`nvidia-smi` should show no other large process; Granite needs about 6.8 GB of 8 GB), then `make models` once and `make warm`.

## The script (replay)

| Time | Show | Say |
| --- | --- | --- |
| 0:00 | Architecture diagram | "A Compose stack: FastAPI app, a runner, PostgreSQL, the Bifrost gateway for local and cloud models, Ollama on the GPU. One service layer behind the browser, the API, the CLI, MCP and CI." |
| 0:30 | Home → open the **recorded example** check | "A real session we recorded. A friendlier prompt for a support-triage bot. Would you approve it?" |
| 0:45 | The verdict: **FAIL** and the limits table | "+1,045 % cost per request, 6/8 correct → 0/8, so no successful task at all. Two limits we set: cost may rise 20 % at most, and no fewer correct answers than the baseline. Both broken." |
| 1:10 | What changed → Why the cost changed → Case by case | "The prompt diff; output went from 2 to about 230 tokens per case; here are the verbose answers that break the label." |
| 1:40 | **What if prices change?** → try `0.8` / `4.0` | "The same measured tokens at a cloud provider's prices. Behavior is held fixed, so this is repricing, not a prediction." |
| 2:00 | **Explain and fix** | "The analyst doesn't start from nothing: CostGuard hands it the evidence it already computed. Two model calls, a fifth of a cent. Every number it quotes is checked against that evidence." |
| 2:30 | The scoreboard: three fixes | "It proposed three different prompts. We measured all three against the **original** baseline under the same limits: 0 %, −11 % and −20 % cost per request. The highlighted one is not the cheapest request: Fix C is, but it gets one answer wrong, so Fix B pays less per **successful task**. The model proposes, the measurements decide." |
| 3:15 | Open the winning fix check | "Same page, same limits: it passes. A fix is just another check." |
| 3:30 | GitHub Actions run → Summary | "The DevOps part: on every push CI builds the image, runs the tests and the cost gate on recorded runs. The verbose candidate is blocked, the fix passes. The policy is a JSON file in the repo." |
| 4:00 | Terminal: `make gate` | "Same check locally: exit 1 blocks, exit 0 passes, exit 2 means inconclusive evidence." |
| 4:15 | **New check** → switch to *Refund replies* | "Not everything has one right label. Here a judge model scores each reply against a rubric, shows its reason, and its own cost is reported separately. If the judge gives no verdict, the quality limit becomes inconclusive, never a pass." |
| 4:40 | Close | "Decisions are deterministic and priced on an explicit catalog; the LLMs generate evidence and hypotheses, and each is measured. Local-first prototype: no production traces or auth." |

## Live variant

1. **New check** keeps the support-triage preset: choose `ollama/llama3.2:3b-instruct-q4_K_M` and **Run the check**. Answers appear as they arrive (one word from the baseline, paragraphs from the candidate); the check page opens by itself.
2. Talk through the verdict while **Ask the analyst why** runs (2–3 minutes on AC power).
3. **Test the fixes** (three runs of 8 cases, about 30 s) and watch the scoreboard fill in.
4. If a fix fails, that is the point: only measured fixes win. Open it and ask the analyst again there.

## If something goes wrong

| Problem | Do this |
| --- | --- |
| A live run is slow or stuck | Switch to the recorded example; it needs no model |
| The analyst stops with a timeout (HTTP 504) | The page names the reason. Plug the laptop in; use the recorded example |
| The gateway shows unavailable on **New check** | `make up`, check `BIFROST_PORT` and `docker compose --profile local ps` |
| The home page is crowded with old checks | They stay in PostgreSQL by design; open the one tagged "recorded example" |
| No network for GitHub | Show `make gate` only |

## Numbers to quote (recorded session)

- Candidate vs baseline: +1,044.82 % cost per request ($0.89 → $10.22 per month at 100k requests), 6/8 → 0/8 correct, output 2 → 226 tokens per case.
- Analyst: 2 calls, $0.0022 at the notional rates; it proposed three prompts of 30, 22 and 16 words (the baseline's is 30).
- Fixes measured against the original baseline: cost per request 0 % (6/8 correct), −11.20 % (8/8), −20.17 % (7/8); per successful task 0 %, −33.4 %, −31.6 %. All pass, and Fix B is highlighted.
- Across two live sessions 5 of 6 proposed fixes passed; the other scored 5/8 and failed. A small sample.
- CI on Groq at catalog prices: candidate +2,204 % and quality 1 → 0 (blocked); the fix passes; the job takes about 1.5 minutes.
- Judge (refund preset, `llama3.2:3b` as judge): the check fails on cost (+387 %), while a 3B judge accepted "3–5 business days" for a "within 5 business days" rubric, so say plainly that a judge is only as good as its model.

## Likely questions

- **Why not just trust the LLM's explanation?** It can be wrong, so every fix is measured. Pass/fail and money come from the deterministic engine, and quoted numbers are checked against the evidence.
- **Why compare quality against the baseline instead of a fixed score?** It's a regression test. The 3B model misroutes two messages even with the good prompt, so a fixed 85 % bar would fail every fix.
- **Why temperature 0?** So the baseline and candidate differ because of the prompt, not random sampling.
- **Are these real prices?** Local inference has no per-token bill, so a live check prices the model at its OpenRouter rate (Llama 3.2 3B: $0.05 / $0.33 per million tokens), and cloud models are priced there too. The recorded example was captured at notional $0.10 / $0.40 rates and keeps them; the CI evidence uses Groq's catalog prices. Every report names its price catalog.
- **What is Bifrost for?** One OpenAI-compatible gateway for Ollama and cloud providers. Provider keys stay there, not in CostGuard.
- **Is the recorded example fake?** It is a real session exported with `python -m costguard record`; only the observations are stored and the verdicts are recomputed on load. The page says it is a replay.
- **How would I add observability or my own tool?** One function: `events.add_sink` for observability, `@tool` for an analyst tool, `@register` for an evaluator, a function in `mcp_server.py` for MCP. See the README's "Extending it".
- **What isn't covered?** Production traces, automatic instrumentation, RAG, authentication. It's a single-user local prototype.
