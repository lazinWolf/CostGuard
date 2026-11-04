# Rethinking the core: from regression gate to unit economics

Follows [FINDINGS.md](FINDINGS.md) and [POSITIONING.md](POSITIONING.md). Those assumed the core
(a baseline-vs-candidate cost gate) was right and fixed its setup. This note questions the core.
Market claims here are reasoning, not validated with users or competitors; see "How to find out".

## Why the regression gate is a weak product on its own

1. **Prompt-change cost is mostly predictable.** A prompt that adds 800 tokens costs 800 tokens
   more per call; anyone can count that. The gate tells people what arithmetic already told them.
2. **The bills that hurt come from behavior and usage, not edits:** an agent loop that never ends,
   a conversation whose context grows every turn, retries, a free-tier user who costs $300, one
   tool call fanning out into twenty. A two-version diff on eight cases does not see these.
3. **Quality is the hard half and it is bolted on.** The scoring is exact match or a model judge on
   hand-written cases. Evaluation tools are mature; competing there on a thin scorer loses.
4. **Habit.** People change prompts and models occasionally. A tool used a few times a year does
   not become part of a workflow.
5. **Trust.** A pass/fail on 8 cases, one run each, will not block a PR a founder cares about.

## What builders actually fear

"**Am I losing money on each user?**" Specifically:

- Before launch: what does an average user cost, what does the p95 user cost, and at what price
  or usage cap does the plan make money?
- After launch: which users, features and flows cost the most, and is any plan underwater?
- Always: what is one *successful outcome* (a resolved ticket, a generated report) costing me?
  CostGuard's best existing idea, cost per successful task, is exactly this unit.

That is a business question with a recurring answer, not a one-off diff.

## Proposed product: unit economics for AI features

| Layer | Question | Needs |
| --- | --- | --- |
| **Know** | What does a user, feature or outcome cost? | Real calls tagged with user, feature, plan (a proxy or client wrapper). Table stakes; others do it, so keep it cheap. |
| **Forecast** | What happens at 10x users? Is the free tier underwater at $0/month? Cost of the p95 user? | A usage-distribution model from observed traffic. Shows margin at a stated price. |
| **Act** | What would actually reduce it, and is it safe? | Cheaper model, routing, caching, shorter context, **verified by replaying real traffic with quality scored**. This is where the existing engine earns its place. |
| **Guard** | Stop surprises. | Per-user and per-feature budgets, loop and runaway detection, alerts; regression gate in CI as one check. |

The day-one output is a finding, not a diff, from a single day of traffic with no cases and no
baseline: *"Your free tier loses $4.20 per active user. The top 1% of users are 38% of spend. 82%
of input tokens are the same 1,400-token prompt."* A founder acts on that the same afternoon.

## What is kept, what is demoted

- **Keep as the trust layer:** exact decimal pricing, matched-case comparison, cost per successful
  task, "inconclusive" instead of a false pass, dated prices. These make "Act" recommendations
  verifiable, which generic "use a cheaper model" advice is not.
- **Demote:** the CI gate (one Guard check), the LLM analyst (optional explain), Postgres/Bifrost/
  Grafana as prerequisites.
- **New:** capture with user/feature tags, spend breakdowns, forecast and margin, budgets.

## Honest risks

- "Know" is crowded (gateways and observability tools show cost per user). Differentiation must
  come from margin and forecast framing and from verified, quality-checked savings, not from
  dashboards.
- Solo builders pay little; the buyer is likely a small team whose margin matters. Unproven.
- Forecasts from a few days of traffic can mislead; they must show their own uncertainty.

## How to find out (before building much)

1. Talk to 5-10 people shipping an AI feature. Ask what they last did about cost, what it cost them,
   and what they would pay to know. Do not describe this product first.
2. Hand-build the day-one report for one real app's logs. If the owner changes something because
   of it, the "Know + Forecast" hook is real. If they shrug, stop.
3. Only then build capture, and keep the replay engine as the "Act" step.
