# Why would anyone use CostGuard, and how does it fit a real flow?

Written from the builder's seat after trying it ([FINDINGS.md](FINDINGS.md)). Competitor remarks
are from general knowledge and not verified here.

## Honest starting point

Today, no one would. A solo builder gets to a first useful answer only after installing Docker,
Postgres, a gateway and a local model, writing cases by hand, typing prices and picking a limit.
Everything CostGuard does well (exact money, "inconclusive" instead of a false pass, cost per
successful task) sits behind that wall. The README leads with the idea, then the stack, so the
first impression is a research project.

## The questions builders actually ask, ranked by pull

1. **"Can I switch to a cheaper model?"** Frequent (new models and price cuts every few weeks),
   high value (often 5-20x), and answered today by vibes. Dashboards cannot answer it; they show
   what you spent, not what a different model would have done on your traffic. Eval tools can, but
   you write the cases and read a matrix. **This is the wedge.**
2. **"Where is my money going?"** Real, but commodity: observability and gateway products already
   show spend. Worth having only as the free first step that feeds question 1.
3. **"Did this PR make us more expensive?"** Real for teams and for AI-assisted coding, where an
   agent can quietly add 3k tokens of context to every request. Lower pull for a solo builder.

CostGuard's own edge is the pairing of cost **and** correctness per successful task: "mini is 94%
cheaper but 2 of 8 answers are wrong" is the actual decision, and most tools show only one half.

## The flow that would make someone bother

```
pip install costguard
costguard init                 # detects openai/anthropic in the repo, sets up capture
python app.py                  # run the app as usual (dev, staging or a day of prod)
costguard report               # $/month, input vs output, repeated prefix, retries, top calls
costguard try gpt-4o-mini claude-haiku-4-5 gemini-flash
```

`try` replays the captured real requests on each alternative and prints a **receipt**:

| Model | $/month | Agrees with current | p95 latency | Verdict |
| --- | ---: | ---: | ---: | --- |
| current (gpt-4o) | $217 | n/a | 1.9 s | |
| gpt-4o-mini | $13 | 71% | 1.2 s | not safe everywhere |
| mini + route refunds to gpt-4o | $58 | 96% | 1.4 s | **saves $159/mo** |

The routing line is computed, not guessed: which request types the cheap model gets right. Each
row links to the requests that differed, side by side, so the number can be checked. That page is
the thing people screenshot and share.

Then the existing machinery earns its place: `gate` in CI as a PR comment when prompt or model
config changes, and an MCP tool so a coding agent asks "what does this change cost?" before it
edits a prompt.

## What builds trust (the signal)

- **Receipts, not scores.** Show the actual cases that changed.
- **State uncertainty.** Repeat runs and report intervals; say "inconclusive" when evidence is thin.
  CostGuard already does the latter well.
- **Local and private.** Prompts are stored on the user's machine, keys never leave it, with
  redaction on capture. Stated at the top of the README.
- **Dated, sourced prices** from a built-in table, not typed by hand.
- **Works in 60 seconds with no keys:** a bundled recorded dataset so `costguard try --demo` prints
  a receipt immediately.

## What to cut or demote

- Postgres, Bifrost, Grafana and Ollama as prerequisites. Files or SQLite; any OpenAI-compatible
  endpoint.
- The LLM analyst as the headline. A small local analyst is not what draws anyone in; the
  deterministic routing suggestion is worth more. Keep it as an optional "explain".
- Hand-authored cases as the default. Real traffic is the default; hand cases are the fallback.

## Build order

1. `try` + `report` on a JSONL of real requests (OpenAI-format logs). Proves the receipt with no
   plumbing. If this table is not compelling on real data, nothing downstream matters.
2. Capture: a logging proxy or one-line client wrapper that writes that JSONL.
3. `pip install`, built-in price table, no database.
4. PR comment Action and the MCP "what will this cost" tool, reusing the existing gate.
5. Repeated runs and intervals.
