# Market, competitors and user needs (research, 2026-11-04)

Sources are web results from this session: vendor blogs, news, docs and forum posts. Where I found
only one source, or sources disagreed, it says so. Nothing here is an interview. Treat market-size
numbers as third-party estimates. Extends [WHO-AND-WHY.md](WHO-AND-WHY.md).

## 1. The landscape in one table

| Category | Players found | What they do for cost | Weakness reported |
| --- | --- | --- | --- |
| **Gateways / proxies** | [LiteLLM](https://docs.litellm.ai/docs/proxy/users), [Portkey](https://portkey.ai/docs/product/administration/enforce-budget-and-rate-limit), [Helicone](https://mintlify-landing-page.vercel.app/blog/mintlify-acquires-helicone), [OpenRouter](https://openrouter.ai/blog/insights/governing-team-ai-spend), Bifrost, TrueFoundry | Per-key, per-user and per-team budgets, rate limits, spend dashboards | Self-hosting burden (proxy, database, cache), latency and stability under load per competitor articles ([1](https://dev.to/kamya_shah_e69d5dd78f831c/8-best-litellm-alternatives-for-teams-outgrowing-a-python-proxy-36mf), [2](https://vercel.com/i/litellm-vs-openrouter)); OpenRouter per-key caps do not coordinate across keys and have no per-end-user cap in what I found |
| **Observability and evals** | [Langfuse](https://langfuse.com/resources/engineering/llm-cost-management), LangSmith, Braintrust, Arize Phoenix, W&B Weave, Datadog LLM Observability | Cost per trace and span; guides for per-user attribution ([Braintrust](https://www.plushcap.com/content/braintrust/blog/braintrust-how-to-track-llm-costs-2026-a-playbook-for-per-user-per-feature-and-per-agent-run-attribution), [Traceloop](https://traceloop.com/blog/from-bills-to-budgets-how-to-track-llm-token-usage-and-cost-per-user)) | Langfuse self-hosting needs Postgres, ClickHouse, Redis and S3; pricing jumps at scale per reviews ([review](https://enterprisedna.co/resources/blog/practitioner-langfuse-review)). Cost is a feature, not the product. |
| **AI FinOps** | [Vantage](https://vantage.sh/blog/best-finops-tools-ai), [DoiT Attribute](https://www.doit.com/ai-cost-attribution-guide-for-finops), Datadog Cloud Cost, CloudZero (no detail found), nOps | Allocate AI spend to customer, feature, agent; reconcile with invoices | Aimed at finance and platform teams at larger companies. Provider invoices carry no per-customer data, so tools need runtime telemetry ([DoiT](https://www.doit.com/blog/ai-attribution-why-tagging-fails-on-tokens)). |
| **Routers** | [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/) (open), Martian, Not Diamond, OpenRouter auto | Send each request to a cheaper model when safe | Savings are self-reported or from benchmarks, not production audits; 20-50 ms added latency reported for one ([directory](https://www.llmreference.com/best/llm-routers)) |
| **Shadow and replay** | [LiteLLM shadow evals](https://docs.litellm.ai/blog/auto-router-shadow-evaluations), [FutureAGI](https://docs.futureagi.com/docs/command-center/features/shadow-experiments), open-source replay scripts | Test a candidate model on mirrored or recorded traffic | Replay variance may not predict production ([caveat](https://tianpan.co/blog/2026/06/01/the-trace-replay-your-new-model-cannot-trust)); mostly inside bigger platforms |
| **Loop and budget breakers** | [agent-fuse](https://pypi.org/project/agent-fuse), [agent-watchdog](https://pypi.org/project/agent-watchdog/), [LongGuard](https://pypi.org/project/longguard/), [budgetctl](https://pypi.org/project/budgetctl/) | Stop repeated tool calls and cap per-run spend | Tiny, early projects; in-process libraries cannot see across runs; a half-executed run after a stop is an open problem ([AutoGen thread](https://github.com/microsoft/autogen/discussions/8135)) |
| **Coding-agent cost** | [ccusage](https://www.mintlify.com/ryoppippi/ccusage) and ports, Claude Code `/usage`, usage monitors | Read local session logs and report cost | Estimates only, local only, per machine |

## 2. What changed in 2026: the independent middle disappeared

- **Langfuse** was acquired by ClickHouse in January 2026 (several sources agree; a separate "$50M Series B" claim conflicts and is unverified) ([landscape](https://moklabs-research.pages.dev/reports/market-analysis/2026-03-19-ai-observability-llmops-market)).
- **Helicone** was acquired by Mintlify, announced March 3, 2026; it stays in **maintenance mode** (security and bug fixes, new models, help migrating) ([Mintlify post](https://mintlify-landing-page.vercel.app/blog/mintlify-acquires-helicone)). Third-party posts say feature work stopped.
- **Portkey** is being folded into Palo Alto Networks' Prisma AIRS security platform; sources disagree on the date (April 30, May 29 or June 2), terms undisclosed ([GovConWire](https://www.govconwire.com/articles/palo-alto-networks-portkey-acquisition-prisma-airs)). It now points at enterprise security.
- **LiteLLM** had two malicious PyPI releases (1.82.7 and 1.82.8) on March 24, 2026 that harvested credentials; several security firms report it, and the entry point is disputed ([NetSPI](https://www.netspi.com/?p=32614), [Comet](https://www.comet.com/site/blog/litellm-supply-chain-attack/)). Clean 1.82.6 is the last good version reported.

**Inference (mine, not a sourced claim):** the small-team-friendly, independent, simple options were absorbed or shaken within about six months, which leaves a plausible opening for a lightweight independent tool. Helicone users now searching for an alternative are a concrete, reachable audience ([example](https://dev.to/torrixai/helicone-is-now-in-maintenance-mode-here-is-how-to-switch-to-a-self-hosted-alternative-in-5-4li0)).

## 3. What users actually need (synthesis)

| Need | Evidence | Strength |
| --- | --- | --- |
| **Per-user and per-feature attribution.** Provider invoices and dashboards cannot say which customer is unprofitable | [DoiT](https://www.doit.com/ai-cost-attribution-guide-for-finops), [Credyt](https://dev.to/credyt/how-to-track-llm-costs-per-customer-in-production-4bpm), [Portkey](https://portkey.ai/docs/guides/use-cases/track-costs-using-metadata), a Reddit thread ([snapshot](https://reddit.sentinel-team.org/posts/1r5nnlh/snapshots/2026-02-27T16%3A00%3A16.74859Z)) | strong |
| **It starts with a tag the app must send** (user id, feature). Without it only aggregates exist | [Traceloop](https://traceloop.com/blog/from-bills-to-budgets-how-to-track-llm-token-usage-and-cost-per-user), [DEV](https://dev.to/mukundakatta/track-llm-cost-per-user-request-in-your-production-agent-3387) | strong |
| **Enforcement, not just charts** (a hard stop before the provider bills) | [Portkey](https://portkey.ai/blog/budget-limits-and-alerts-in-llm-apps), [Helicone](https://theneuralbase.com/helicone/learn/intermediate/cost-threshold-alerts/), incident posts in WHO-AND-WHY | medium |
| **Pricing and margin decisions as AI moves to usage-based and hybrid pricing**; one report says 66% of vendors meter AI usage, GitHub Copilot moved to usage billing in June 2026 | [Nalpeiron](https://nalpeiron.com/resources/program/2026-state-of-b2b-software-monetization/ai-feature-pricing-models/), [MEAN CEO](https://blog.mean.ceo/?p=10439) (vendor and consultancy sources) | medium |
| **Safe model switching with evidence** instead of vendor claims | Router savings are self-reported; replay caveats; playbooks advise checking quality at the tails ([playbook](https://tianpan.co/blog/2026-04-10-model-migration-playbook-swap-foundation-models-without-breaking-production)) | medium |
| **Low setup and ownership burden** | LiteLLM and Langfuse self-hosting complaints; Helicone migration posts | medium (mostly competitor-written) |
| **Trust in software that holds API keys** | The LiteLLM compromise | medium to strong |
| **Zero-friction local tools spread** | ccusage runs with `npx`, reads local logs, needs no account, and has ports for several agents | medium (one example) |

Cost is a top-three production concern but not the top one; surveys put integration, debugging and
privacy first ([Qwak](https://www.qwak.com/the-2024-state-of-ai)).

## 4. Market size

Third-party estimates only: the LLM observability platform market at about $2.7B in 2026 growing to
about $9.3B by 2030, and LLMOps at about $4.8B by 2028 ([landscape](https://moklabs-research.pages.dev/reports/market-analysis/2026-03-19-ai-observability-llmops-market)).
Scopes differ and I could not check the primary reports. They say the category is large and funded,
not how much of it is *cost control* for small teams.

## 5. What this means for our plan

1. **Do not lead with a proxy that holds keys.** After the LiteLLM incident, "paste your base URL
   and route all traffic through us" is a harder ask. Make **reading logs and traces** the first
   path, like ccusage: accept OpenAI-style logs, OpenTelemetry, and exports from Helicone and Langfuse.
   A proxy becomes opt-in, for enforcement only.
2. **Make trust a feature.** Standard-library-only (the prototype already is), pinned and signed
   releases, no stored keys, local by default. This is a real contrast with heavy Python proxies.
3. **Target the displaced.** A one-command importer for Helicone and Langfuse exports turns the 2026
   consolidation into an acquisition channel.
4. **Attribution is where everyone falls short, so make it nearly free:** derive feature and session
   from the request, and make adding `user_id` a one-liner. Own the "tag problem".
5. **Verified savings is the open trust gap in routing.** Everyone claims savings; few show a receipt
   on your own traffic. Keep the replay engine for exactly that.
6. **Do not compete with enterprise platforms.** Datadog, Vantage, DoiT and Palo Alto own large
   accounts. The target stays solo builders and teams of 2-20.

## 6. Unknowns that could break the thesis

- Whether the consolidation actually left users stranded, or whether they just moved to LangSmith,
  Braintrust or OpenRouter.
- Whether small teams will pay for cost control at all, given free gateways and free `/usage` style tools.
- Whether Datadog, Vantage or the gateways will ship the missing pieces first.
- Source quality: much of the competitor criticism is written by competitors, and I did not run any of these tools.

## 7. Next research that would settle it

1. Read the open issues and discussions on LiteLLM, Langfuse and the loop-breaker repos for what users ask for most.
2. Try two or three tools hands-on, as the builder persona, and write down the time to first useful alert.
3. Find 10 people who posted incidents or wrote their own breaker and ask the three questions in WHO-AND-WHY.
4. Check pricing pages (Langfuse, LangSmith, Braintrust, OpenRouter) for the price a small team actually pays.
