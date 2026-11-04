# Who would use it, and why: evidence from the web

Eight searches on 2026-11-04. Most sources are blogs, vendor pages and forum posts, so figures are
**reported, not verified**, and several come from companies selling a fix. No interviews were done.
Each claim carries a strength: **strong** (many independent sources agree), **medium** (a few, or
partly vendor-written), **weak** (one source or inference).

## The pain is real, and it has two shapes

**1. Incidents (fear).** Runaway agents and abused endpoints produce huge bills, found late.
- A two- to four-agent loop is reported to have run 11 days for about $47,000 ([post](https://dev.to/dingdawg/how-an-ai-agent-ran-up-a-47000-bill-in-11-days-and-how-to-stop-it-1fk), [postmortem](https://dev.to/gabrielanhaia/the-agent-that-spent-47k-on-itself-an-autonomous-loop-postmortem-3313)); a Bedrock agent loop about $30,000 with anomaly detection not firing ([report](https://aiweekly.co/alerts/aws-bedrock-agent-loop-costs-developer-30000)). Single-source anecdotes; some larger headlines had no primary evidence. **Strength: medium** (the pattern is consistent, individual figures are not checked).
- Small builders report surprise bills and abuse of public endpoints ([Lovable apps](https://dev.to/jacefromhi/rate-limiting-for-lovable-apps-how-to-stop-surprise-openai-bills-5682), [surprise AI bills](https://dev.to/johns23424234324234/the-surprise-ai-bill-problem-is-killing-small-products-3298), [one customer blowing the budget](https://dev.to/amedinat/how-to-stop-one-customer-from-blowing-up-your-entire-llm-budget-4mac)). **Strength: medium.**
- Provider limits are org or project wide, not per user, and some users report spend caps that only warn ([OpenAI community thread](https://community.openai.com/t/someone-is-abusing-openai-api-through-a-bug/538451), [guide](https://vibe-eval.com/fix/openai-bill-spike-api-abuse/)). **Strength: medium**; verify current OpenAI behavior.

**2. Margin (slow dread).** AI apps have thinner margins than SaaS, and the free tier and heavy users drive it.
- AI-native gross margins reported around 50-60% against 70-90% for mature SaaS, secondhand from ICONIQ and Bessemer ([summary](https://www.softwareseni.com/why-ai-gross-margins-are-so-much-lower-than-saas-and-what-that-means-for-your-business/), [framework](https://www.thrad.ai/content/ai-app-unit-economics-and-monetization)). **Strength: medium**; check the primary reports.
- "Free-tier users are most of the inference cost and almost none of the revenue"; the P90 user decides the margin ([thrad](https://www.thrad.ai/content/ai-app-unit-economics-and-monetization), [why founders are killing free tiers](https://building.it.com/posts/why-founders-are-killing-free-tiers-2026)). **Strength: medium**, vendor-flavored.

**Caution on size.** Surveys rank cost second or third among production concerns, behind integration,
debugging and privacy; usage and cost tracking was a challenge for 42% in one 2024 survey
([Qwak](https://www.qwak.com/the-2024-state-of-ai), [academic study](https://arxiv.org/html/2408.05002v4)).
Cost is a **painkiller only at the moment of an incident and a vitamin the rest of the time.** The
product has to be installed before the incident, which is a hard sell. **Strength: medium.**

## What already exists (this changes my earlier claim)

I wrote in [PRODUCT.md](PRODUCT.md) that there was a gap in budgets, loop stops and verified switching.
The searches show each piece exists:

| Need | Existing options found |
| --- | --- |
| Per-user and per-key budgets | [LiteLLM](https://docs.litellm.ai/docs/proxy/users) (per key, user, end user, model); [Portkey](https://portkey.ai/docs/product/administration/enforce-budget-and-rate-limit) hard caps (Enterprise and some Pro); [Helicone](https://theneuralbase.com/helicone/learn/intermediate/cost-threshold-alerts/) alerts and 429 limits (alerts reportedly paid) |
| Agent loop and budget breakers | Small open-source libraries: [agent-fuse](https://pypi.org/project/agent-fuse), [agent-watchdog](https://pypi.org/project/agent-watchdog/), [LongGuard](https://pypi.org/project/longguard/), [budgetctl](https://pypi.org/project/budgetctl/), and a [LiteLLM circuit-breaker write-up](https://dev.to/amih/a-litellm-circuit-breaker-that-kills-runaway-agent-runs-3fp6). None looks dominant. |
| Replay or shadow-test a new model | [LiteLLM auto-router shadow evaluations](https://docs.litellm.ai/blog/auto-router-shadow-evaluations), [FutureAGI shadow experiments](https://docs.futureagi.com/docs/command-center/features/shadow-experiments), open-source replay pipelines ([example](https://dev.to/mukundakatta/replay-every-llm-prompt-against-a-new-model-before-you-migrate-1823)), and playbooks ([model migration](https://tianpan.co/blog/2026-04-10-model-migration-playbook-swap-foundation-models-without-breaking-production), [replay caveats](https://tianpan.co/blog/2026/06/01/the-trace-replay-your-new-model-cannot-trust)) |
| Per-user and margin reporting | Cost tracking in gateways and observability tools; blog frameworks for the margin math; no integrated tool found (not proven absent) |

**No single feature is unclaimed.** But several tiny, separate libraries and write-ups for loop
breakers is itself a demand signal: people are building this themselves and none has won. The opening
is **integration and effort**: one small tool that does the seatbelt, the explanation and verified
savings, for people who will not run a gateway stack. That is a weaker and more honest claim than "a gap".

## Who would use it

| Segment | Why they would | Evidence | Would they pay? |
| --- | --- | --- | --- |
| **Solo and vibe-coded app builders** (Lovable, Bolt and similar, public AI endpoints, free tiers) | Surprise bills and abuse; one heavy user costs more than ten payers; provider limits do not isolate users; they will not run a gateway stack | medium | Low. Best as the free, viral entry point. |
| **Small AI-native startups (2-20 people)** with a free tier and a paid plan | Margin and pricing decisions: who is underwater, what to cap, what to charge. They have no platform team. | medium | **Most likely.** Margin is a money question. |
| **Agent builders** (support, research, coding agents) | Runaway loops and context growth are where the large bills come from | medium for the pain; DIY fixes common | Medium. Many solve it in code. |
| **Teams about to switch models** | New models and price cuts arrive constantly; replay is established practice; tooling is emerging | medium | Medium, but one-off unless it is continuous. |
| **Heavy users of AI coding tools** | Reported surprise bills on tools such as Cursor ([post](https://dev.to/adioof/cursor-3-users-are-getting-2000-bills-nobody-read-the-pricing-page-2ako)) and runaway Claude Code sessions ([post](https://dev.to/devtoolpicks/ai-agents-are-burning-through-bills-overnight-how-to-stop-a-runaway-claude-code-session-201g)) | weak; not in scope today | Unknown. A bigger, different audience worth a look. |

**Who would not:** large enterprises (they buy a gateway or platform), hobbyists with a $10 bill, and
teams with no LLM spend yet.

## Reasons, in the user's words (the job to be done)

- "I want to stop worrying that something will burn my budget while I sleep." (Guard)
- "I want to know which customers and features lose me money, and what to cap." (Know)
- "A cheaper model came out; tell me if I can switch without breaking things, and show me." (Optimize)
- "I want this without running Postgres, Redis and a gateway." (setup cost; the strongest differentiator from existing tools)

## What the evidence does not tell us

- Whether anyone would **install** it, keep it running or pay. Posts show pain and DIY fixes, not willingness to adopt a new tool.
- How good the existing tools are, or how many users they have.
- Current behavior of provider limits and gateway features; check vendor docs.
- The size of each segment. Several sources sell the fix they describe.

## Cheapest ways to find out

1. **Mine the people who already hurt.** The posts above name authors who suffered an incident or wrote their own breaker. Ask five what they did, what it cost them and what they would have wanted. Do not pitch.
2. **Test the setup claim.** Give a rough two-minute proxy to five builders and measure how many reach a first useful alert without help.
3. **Try the wedge copy on a landing page** and count sign-ups: "Stop a runaway agent or a heavy free user from burning your budget. One line, no gateway."
4. **Look at the AI-coding-tool segment** (above); it may be larger and underserved.
