# Explorer: using CostGuard as a builder

**Method.** I played a solo developer with a small support bot ([support-bot](support-bot/)): a
system prompt with a policy document, one tool, 8 tickets, and a fake OpenAI server. I tried to
answer the questions such a person actually has, using only what CostGuard offers. Prices and
token counts come from a fake server, so this tests the workflow, not real numbers.

Variants tried: baseline `gpt-4o`; a "warmer" prompt; `gpt-4o-mini`.

## What worked

The comparison engine is right. It showed the warm prompt costs +70.5 % per request ($217 → $369
a month at 300k requests) at equal quality, and that `gpt-4o-mini` costs −94 % ($217 → $13) but
quality falls 1 → 0.75. The numbers, exit codes and Markdown summary are good and CI-ready.

## Where it is too thin

| # | Friction as a builder | Why it matters |
| --- | --- | --- |
| 1 | **Not installable.** No `pyproject.toml`; I used `PYTHONPATH`. Even `gate` imports SQLAlchemy, psycopg and FastAPI. | Nobody adopts a tool they can't `pip install`. |
| 2 | **~40 lines of glue** to capture one run: wrap each case, time each call, hand-type a price catalog, hand-score, write JSON. The docs give the hook one sentence. | Setup cost exceeds the value of the answer. |
| 3 | **I had to invent the test cases.** A real app has weeks of real requests in logs; CostGuard cannot read them. | Invented cases are why results feel toy-like. |
| 4 | **The hook records no prompts or outputs** (`output` was `None` in the artifact). | After the mini run said "quality −25 %", nothing told me which tickets failed or what it said. |
| 5 | **The answer stops at FAIL.** Mini is 94 % cheaper and wrong on 2 of 8; the useful output is "mini handles 6 of 8 ticket types; route the refund-window ones to gpt-4o and save ~$X". | A builder wants a decision, not a gate. |
| 6 | **Pairwise only.** Three variants meant three separate runs against one baseline. No table of N models/prompts on cost vs quality. | "Which model should I use?" is the most common question. |
| 7 | **No "where does my money go?"** 87 % of my spend was input tokens, almost all the same policy text on every call. The report never says so, and never suggests prompt caching, a shorter context or a cheaper model. | That is the highest-value insight, and it needs no baseline. |
| 8 | **Policy and prices are manual.** I had to guess a 20 % limit and type prices from the vendor page. | Every blank config is a place to give up. Needs defaults and a built-in price table. |
| 9 | **8 cases, one run each**, no variance or confidence. | A verdict on thin evidence looks more certain than it is. |

## Direction

The product answers "did this change break my budget?" only after the user has built cases,
instrumentation, prices and a policy. Builders ask earlier and simpler questions: where is my
money going, could a cheaper model do this, what will this change cost. Proposed order:

1. **Install and capture with no glue.** `pip install`, no database (files or SQLite), a built-in
   price table, and a capture path that needs no code changes: an OpenAI-compatible logging proxy
   (`costguard proxy`) or a one-line client wrapper. Store prompts and outputs.
2. **`costguard report` on real traffic.** Spend by model, prompt and tag; input vs output; shared
   prompt prefix (caching candidate); most expensive calls; projected monthly cost from the
   observed rate. Useful on day one, with no baseline.
3. **`costguard try`: replay real traffic on alternatives.** Re-run captured requests on other
   models/prompts, score agreement with the original answers (judge or similarity), and print a
   cost-vs-quality table with a per-case routing suggestion and the saving.
4. **Then** the existing gate, fed by (3), as a GitHub Action PR comment; analyst explanations;
   repeated runs and intervals so a verdict states its own uncertainty.
