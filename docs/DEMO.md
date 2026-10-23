# CostGuard demo script (about 5 minutes)

The story: a prompt change that looks harmless in review makes every request ten times more expensive and breaks the output. CostGuard measures it, blocks it, has an analyst model explain it and propose a fix, and measures the fix too, in the browser and in CI.

Timings were measured on the RTX 3070 Ti laptop with `llama3.2:3b` as the workload and `granite4.2:8b` as the analyst.

## 30 minutes before

1. Free the GPU: `nvidia-smi` should list no other large GPU processes. Granite needs about 6.8 GB of the 8 GB. Partly on the CPU, its calls can time out (HTTP 504).
2. `make up`. If it fails with `address already in use` on 8080, set `BIFROST_PORT=8081` in `.env` (glance uses 8080 on this laptop).
3. `make restore` loads the recorded session from `demo/snapshot.sql`, with Granite as the analyst. This replaces the database, so run `make snapshot SNAPSHOT=mine.sql` first if you want to keep what is there.
4. Warm the workload model: `docker compose --profile local exec ollama ollama run llama3.2:3b-instruct-q4_K_M ok`.
5. Open these tabs: the overview at <http://localhost:8000>, the latest GitHub Actions run (its **Summary** page), a terminal in the repository, and `docs/costguard-architecture.svg`.

## The script

| Time | Show | Say |
| --- | --- | --- |
| 0:00 | Architecture diagram | "Compose stack: FastAPI app, a runner, PostgreSQL, the Bifrost gateway for local and cloud models, Ollama on the GPU. Same engine in the browser, the CLI and CI." |
| 0:30 | Overview → model `ollama/llama3.2:3b-instruct-q4_K_M` → **Run baseline and candidate** (leave the analyst box unticked) | "Eight support messages, two prompts. The candidate is friendlier and more thorough. Would you approve it?" Answers appear live: the baseline returns one word, the candidate writes paragraphs. About 75 s. |
| 1:45 | The report: **FAIL** | "+1,009 % cost per request, 6/8 correct → 0/8. Blocked on two limits: cost may rise 20 % at most, and quality may not fall below the baseline." |
| 2:00 | What changed → Why the cost changed → Case by case | "The prompt diff; output tokens went from 2 to about 220 per case; here are the actual verbose answers that break the label." |
| 2:20 | **What if prices change?** → enter `0.8` / `4.0` → Recalculate | "Same measured tokens at a cloud provider's prices: the monthly difference at 100k requests. Behavior is held fixed; this is repricing, not a prediction." |
| 2:40 | **Ask the analyst why** → Start investigation | "A separate model with four read-only tools. It can't run anything; it reads evidence and proposes one prompt." It takes 3–4 minutes, so continue while it runs. |
| 2:50 | GitHub Actions run → Summary | "The DevOps part: on every push, CI builds the image, runs the tests and the cost gate on recorded Groq runs. The verbose candidate is blocked, the fix passes; policy is a JSON file in the repo." |
| 3:20 | Terminal: `make gate` | "Same check locally: exit 1 blocks, exit 0 passes, exit 2 means inconclusive evidence." |
| 3:40 | Investigation page (live, or the recorded one under **Recent investigations**) | "Findings first. CostGuard checks every number the model quotes against the evidence it inspected (✓ or ⚠), and each evidence chip jumps to the case row." |
| 4:10 | The recorded first investigation → **RESULT: THE FIX FAILS** | "The analyst's first fix kept an optional restatement; measured, it scores 2/8. CostGuard does not trust the explanation, it measures the fix." |
| 4:30 | **Investigate why it failed** → the second investigation → **analyst-fix-2: PASS** | "Second look: the restatement is the problem; the fix reverts to the strict prompt. Same cost and quality as the baseline: pass." |
| 4:50 | Close | "Decisions are deterministic and priced on an explicit catalog; the LLM generates hypotheses and every one is measured. Local-first prototype: no production traces or auth." |

## If something goes wrong

| Problem | Do this |
| --- | --- |
| The live run is slow or stuck | Open **Reports** and use the recorded `triage-candidate-helpful-prompt` report (+1,008.96 %) |
| The investigation is still running at 3:40 | Use the recorded investigations under **Recent investigations**; come back to the live one at the end if it finished |
| The investigation fails (504 or "did not return a structured conclusion") | Say why: small local models are weak at tool calls, and CostGuard refuses prose as a conclusion. Use the recorded one |
| The gateway is unavailable on the overview page | `make up`, then check `BIFROST_PORT` and `docker compose --profile local ps` |
| No network for GitHub | Show `make gate` only |

## Numbers to quote (recorded session)

- Candidate vs baseline: $0.00000893 → $0.00009898 per request (+1,008.96 %), 6/8 → 0/8 correct, output 2 → about 220 tokens per case.
- First fix: +19.61 % cost, 2/8 correct: fail. Second fix (strict prompt): 0 %, 6/8: pass.
- Each investigation: 5 analyst calls, about $0.004 at the notional rates, 3–4 minutes.
- CI on Groq runs at catalog prices: candidate +2,204 % and quality 1 → 0 (blocked); the fix passes; the whole CI job takes about 1.5 minutes.

## Likely questions

- **Why not just trust the LLM's explanation?** It can be wrong; the first fix proved it. Numbers and pass/fail come from the deterministic engine, and findings are number-checked.
- **Why compare quality against the baseline instead of a fixed score?** It's a regression test. The 3B model misroutes two messages even with the good prompt, so a fixed 85 % bar would fail every fix.
- **Why temperature 0?** So the baseline and candidate differ because of the prompt, not because of random sampling.
- **Are these real prices?** Local inference has no per-token bill, so the demo uses notional reference rates. The CI evidence uses Groq's catalog prices. Every report names its price catalog.
- **What is Bifrost for?** One OpenAI-compatible gateway for Ollama and cloud providers. Provider keys stay there, not in CostGuard.
- **What isn't covered?** Production traces, automatic instrumentation, RAG, authentication. It's a single-user local prototype.
