# Product view: what would someone keep using?

Synthesis of the earlier notes, seen as a product and not a feature list. Market claims are
reasoning, not validated; competitor remarks are from general knowledge and unchecked.

## What the earlier notes got wrong

A report people read once is not a product. The unit economics report (see
[DEEPDIVE.md](DEEPDIVE.md)) is good evidence that the analysis works, but it fails the product test
on three counts: it needs capture before it shows anything, it is read at most monthly, and nothing
forces anyone to come back. The old regression gate has the same flaw (used a few times a year).

A tool survives when it has a **trigger** (a reason to open it), a **fast first win**, and a
**loop** (it keeps giving value without effort). Three triggers are frequent and emotional:

| Trigger | How often | Feeling |
| --- | --- | --- |
| The bill spikes, or an agent runs away overnight | rare but unforgettable | fear |
| A new model or price cut ships | every few weeks | "am I overpaying now?" |
| I launch, add a plan, or change pricing | a few times a year | uncertainty about margin |

## The product: a seatbelt that also finds savings

One local proxy, one changed base URL, three jobs that unlock in order as traffic arrives:

| Stage | Job | First value | Needs |
| --- | --- | --- | --- |
| **Guard** (minute 2) | Stop the bleeding: hard budgets per user, feature and day; stop an agent repeating a tool call; fail gracefully (smaller model, shorter context) instead of an outage; alert to phone or Slack | "Safe to leave running tonight" | Nothing but traffic |
| **Know** (day 1) | What does a user, feature and outcome cost? Who is underwater? Where is the waste? | The report prototyped in `unitecon` | User id, session (derivable) |
| **Optimize** (week 1 onward) | **Shadow-test** a sample of real requests on cheaper models and prompts in the background; score agreement with the current answers; tell you when a switch is both safe and worth it, with a receipt; route automatically only on your approval | "mini matches 91% on summaries; switching saves $140 a month" | Enough traffic, a scorer |

Why this order: Guard has the fastest time to value and needs no evals. It also *generates the
data* Know and Optimize need, so the funnel feeds itself. Optimize is the retention engine: every
new model release re-runs the shadow test and produces a fresh, specific "you could save X" message,
which is a reason to return that no dashboard offers.

The name fits: CostGuard guards (stops), explains (knows) and improves (optimizes), with the
existing exact-money engine, replay runner and agreement scorer as the trust layer.

## Where the gap is

> **Correction after research ([WHO-AND-WHY.md](WHO-AND-WHY.md)):** each piece below already exists in some tool. The honest opening is integration and low setup effort, not a missing feature.

- **Provider spend limits** are account-wide and blunt: they stop the whole app, not one user's loop.
- **Gateways and observability tools** show spend and sometimes enforce per-key budgets, but are
  mostly dashboards you must read. Unverified: how well they stop loops or degrade gracefully.
- **Automatic routers** switch models on your behalf, which asks for trust they rarely earn with
  evidence. **A router that shows its receipt, on your own traffic, and waits for your approval** is
  the differentiator: verified savings, not claimed ones.

## Signals that it is worth installing

- Two minutes to first value: `pip install`, one env var, no database, no Docker.
- A 20-second demo: an agent loops, CostGuard stops it at $0.40, the app keeps working.
- A shareable receipt: "a week on my app: stopped a $300 loop, switched summaries to a smaller
  model, bill down 38%".
- Local first: prompts stay on the machine; fail-open so the proxy can never be the outage.
- Honest about uncertainty: says "inconclusive" and shows the changed cases side by side.

## Business shape

Open-source local core. Paid when it leaves one laptop: hosted alerts and dashboards, team budgets,
shared receipts, SSO, retained history. Start by selling to small teams whose AI margin matters, not
to hobbyists who will not pay.

## Risks, in order of danger

1. **A proxy in the request path is a trust and reliability ask.** Prompts and keys pass through it.
   Mitigate: local, open source, fail-open, plus an SDK wrapper for people who refuse a proxy.
2. **Optimize needs a scorer, and good scoring is hard.** Agreement with the current answer is a
   proxy for correctness. Start with tasks that have checkable outputs (classification, extraction,
   structured JSON) and be explicit about the rest.
3. **Crowded space.** If gateways already ship loop stops and graceful degradation, the wedge is
   thin. Check before building.
4. **Nobody has said they want it.** Everything here is reasoning from one simulation.

## What to build first, and how to know it works

Build **Guard plus the first-day report** as one small proxy: budgets, loop stop, alerts, and the
`unitecon` report from what it captured. It is the quickest route to a real user's real traffic,
which is the thing missing.

It is working if, within two weeks, five people who are not us keep it running for a week and one of
them tells us what it stopped or saved. If they install it and remove it, the thesis is wrong and
the cheapest lesson is to learn that now.

## What changes in the existing code

Keep: exact decimal pricing, matched-case comparison, cost per successful task, the replay runner,
the evaluators, the MCP server, the recorded demo idea. Demote: the analyst as the headline.
Replace: Postgres, Bifrost, Grafana and Ollama as prerequisites with a file or SQLite store and any
OpenAI-compatible endpoint.
