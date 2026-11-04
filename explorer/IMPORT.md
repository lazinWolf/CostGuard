# Reading other tools' exports

Follows [MARKET.md](MARKET.md): after the 2026 consolidation, lead with **reading data people
already have** (no keys, no proxy) and make Helicone and Langfuse users an easy audience to reach.

```bash
python -m unitecon report export.csv --plans plans.json --users users.csv   # format is detected
python -m unitecon convert export.json -o calls.jsonl                       # or just convert
```

`users.csv` is `user_id,plan`; exports do not carry plans, and margin needs them.

## What it reads

| Source | Reads | Derives | Cannot get |
| --- | --- | --- | --- |
| **Langfuse** observations (and traces) | model, token usage, start and end time, error level, the tool's own cost; user, session and feature from the trace | prompt prefix and tool signature, from the messages | plan; outcome |
| **Helicone** request export (JSON, JSONL, CSV with JSON columns) | model, tokens, latency, HTTP status, cost, user, session and feature from properties | prompt prefix and tool signature, from the bodies | plan; outcome |
| **OpenTelemetry GenAI** spans | model, tokens, cache-read tokens, times, status, conversation id | nothing | prompt prefix, tool signature and cost: not in the conventions |

Also: model names such as `gpt-4o-2024-08-06` or `openai/gpt-4o` are matched to the price table
(a date or `latest` suffix only, so `gpt-4o-audio` is left unpriced rather than mispriced), and
when the source reports its own cost the report **reconciles** against it and warns if ours differs
by more than 10%.

## How it was checked

The simulated month was exported in all three shapes (`saas-sim/export_as.py`), imported, and every
headline number compared with the native report (`saas-sim/roundtrip.py`):

| | native | Langfuse | Helicone | OpenTelemetry |
| --- | --- | --- | --- | --- |
| total cost, sessions, cost by feature, failed calls, top user, pro margin | same | **identical** | **identical** | **identical** |
| stuck agent sessions | 9 | 9 | 9 | 0 (no tool arguments in OTel) |
| prompt-cache estimate | $2.90 | $2.90 | $2.90 | none (no prompt text in OTel) |

Plus 16 unit tests covering each format, errors, model names and reconciliation. Mutation check:
loosening the model-name rule makes two tests fail.

## Honest limits

- **The fixtures are my reading of public docs, not real exports.** Field names for Langfuse and
  Helicone could not be confirmed (Helicone's official docs use prefixed names like
  `request_user_id`, third-party guides use others; I accept both). The first real export may
  differ. A failed read says which fields it saw and never guesses.
- **Whole files are loaded into memory.** A real export of millions of calls will not fit. JSONL
  and CSV could be streamed; Langfuse JSON pages would need paging. Not done.
- **Prefix length is estimated** as characters / 4, so the caching figure stays an estimate.
- **OpenTelemetry GenAI names are still experimental** and reportedly moved to a new repository
  in June 2026; check the current spec.
- No plan, no outcome, and no price table of your own: the bundled `prices.json` is an assumption.

## Mistakes made on the way (kept for honesty)

- Prices were looked up by the logged model name, so dated names crashed the analysis: found only
  by running real-shaped data. Fixed, with a test.
- Format detection sampled the first 25 records, and Langfuse lists traces first: found by the
  round trip. Fixed.
- A `git checkout` meant to undo a test mutation also discarded uncommitted work in `load.py`.
  Noticed immediately from `git status`, re-applied, tests and round trip re-run clean.
