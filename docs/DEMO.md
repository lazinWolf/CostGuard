# CostGuard demo script (about 5 minutes)

The story: a prompt change that looks harmless in review makes every request ten times more expensive and breaks the output. CostGuard measures it, blocks it, has an analyst model explain it and propose a fix, and measures the fix too, in the browser and in CI.

Timings were measured on the RTX 3070 Ti laptop with `llama3.2:3b` as the workload and `granite4.2:8b` as the analyst.

## 30 minutes before

1. Free the GPU: `nvidia-smi` should list no other large GPU processes. Granite needs about 6.8 GB of the 8 GB. Partly on the CPU, its calls can time out (HTTP 504).
2. `make up`. If it fails with `address already in use` on 8080, set `BIFROST_PORT=8081` in `.env` (glance uses 8080 on this laptop).
3. `make restore` loads the recorded session from `demo/snapshot.sql`, with Granite as the analyst. This replaces the database, so run `make snapshot SNAPSHOT=mine.sql` first if you want to keep what is there.
4. `make warm` loads the workload model, so the first live run doesn't wait for a model load.
5. Open these tabs: the overview at <http://localhost:8000>, the latest GitHub Actions run (its **Summary** page), a terminal in the repository, and `docs/costguard-architecture.svg`.

## The script

| Time | Show | Say |
| --- | --- | --- |
| 0:00 | Architecture diagram | "Compose stack: FastAPI app, a runner, PostgreSQL, the Bifrost gateway for local and cloud models, Ollama on the GPU. Same engine in the browser, the CLI and CI." |
| 0:30 | Overview → model `ollama/llama3.2:3b-instruct-q4_K_M` → **Run baseline and candidate** (leave the analyst box unticked) | "Eight support messages, two prompts. The candidate is friendlier and more thorough. Would you approve it?" Answers appear live: one word from the baseline, paragraphs from the candidate. About 20 s. |
| 0:50 | The report: **FAIL** | "+1,100 % cost per request, 6/8 correct → 0/8. Blocked on two limits: cost may rise 20 % at most, and quality may not fall below the baseline." |
| 1:05 | What changed → Why the cost changed → Case by case | "The prompt diff; output went from 2 to about 240 tokens per case; here are the actual verbose answers that break the label." |
| 1:30 | **What if prices change?** → enter `0.8` / `4.0` → Recalculate | "Same measured tokens at a cloud provider's prices: the monthly difference at 100k requests. Behavior is held fixed; this is repricing, not a prediction." |
| 1:50 | **Ask the analyst why** → Start investigation | "A separate model with four read-only tools. It can't run anything; it reads evidence and proposes one prompt." It takes 2–3 minutes, so continue while it runs. |
| 2:00 | GitHub Actions run → Summary | "The DevOps part: on every push, CI builds the image, runs the tests and the cost gate on recorded Groq runs. The verbose candidate is blocked, the fix passes; policy is a JSON file in the repo." |
| 2:30 | Terminal: `make gate` | "Same check locally: exit 1 blocks, exit 0 passes, exit 2 means inconclusive evidence." |
| 2:45 | Recorded session: **Recent investigations** → the older one → **RESULT: THE FIX FAILS** → **Investigate why it failed** → `analyst-fix-2`: **PASS** | "An earlier run: the analyst's first fix kept a restatement and scored 2/8. CostGuard doesn't trust the explanation, it measures the fix. The second look reverted to the strict prompt: pass." |
| 3:30 | The live investigation | "Findings first. CostGuard checks every number the model quotes against the evidence it inspected (✓ or ⚠), and each evidence chip jumps to the case row." |
| 4:00 | **Run the proposed fix** (about 10 s) | Usually a **PASS** at about the baseline's cost. If it fails, that's the recorded story live: click **Investigate why it failed**. |
| 4:40 | Close | "Decisions are deterministic and priced on an explicit catalog; the LLM generates hypotheses and every one is measured. Local-first prototype: no production traces or auth." |

## If something goes wrong

| Problem | Do this |
| --- | --- |
| The live run is slow or stuck | Open **Reports** and use the recorded `triage-candidate-helpful-prompt` report (+1,008.96 %) |
| The investigation is still running at 3:30 | Stay on the recorded session a little longer; the page refreshes itself when the live one finishes |
| The investigation fails | The page names the reason (for example a timeout or a full context window). Say why: local models are weak at long tool use, and CostGuard refuses prose as a conclusion. Use the recorded one |
| The gateway is unavailable on the overview page | `make up`, then check `BIFROST_PORT` and `docker compose --profile local ps` |
| No network for GitHub | Show `make gate` only |

## Numbers to quote (recorded session)

- Live run (current settings): $0.00000893 → $0.00010713 per request (+1,100.28 %), $0.89 → $10.71 per month at 100k requests, 6/8 → 0/8 correct, output 2 → 238.5 tokens per case.
- Recorded session: +1,008.96 % for the same change. It was recorded with the older 8K-token context; the 8-bit context cache shifts the 3B model's long answers slightly.
- Recorded fixes: the first scored 2/8 at +19.61 % (fail); the second reverted to the strict prompt, 0 % and 6/8 (pass).
- In rehearsal, 6 of 6 live investigations completed, and 2 of the last 3 proposals passed (−3.4 % and 0 % cost); the third brought back the restatement and failed.
- Each investigation: 4–6 analyst calls, about $0.004 at the notional rates, 2–3 minutes.
- CI on Groq runs at catalog prices: candidate +2,204 % and quality 1 → 0 (blocked); the fix passes; the whole CI job takes about 1.5 minutes.

## Likely questions

- **Why not just trust the LLM's explanation?** It can be wrong; the first fix proved it. Numbers and pass/fail come from the deterministic engine, and findings are number-checked.
- **Why compare quality against the baseline instead of a fixed score?** It's a regression test. The 3B model misroutes two messages even with the good prompt, so a fixed 85 % bar would fail every fix.
- **Why temperature 0?** So the baseline and candidate differ because of the prompt, not because of random sampling.
- **Are these real prices?** Local inference has no per-token bill, so the demo uses notional reference rates. The CI evidence uses Groq's catalog prices. Every report names its price catalog.
- **What is Bifrost for?** One OpenAI-compatible gateway for Ollama and cloud providers. Provider keys stay there, not in CostGuard.
- **What isn't covered?** Production traces, automatic instrumentation, RAG, authentication. It's a single-user local prototype.
