# AI unit economics

86,021 calls, 7,491 sessions, 581 active of 800 registered users, 30 days. Costs are per 30 days unless marked.

**Spend $1,003 a month** ($1.73 per active user). Subscription revenue $2,060, margin after model cost $1,057.

## What to do

Grouped by how well each is known, then by dollars a month. They overlap, so they do not add up.

**Fix now: read straight from the log**

1. **49 sessions were stuck repeating the same tool call**: $285 a month. They are 30% of all spend. Counting only calls after the third identical tool call. Stop an agent after 4 identical tool calls or a step budget.
2. **2,047 failed calls were billed for their input**: $23.11 a month. Counts only calls the log marks as failed and records input tokens for. Lower timeouts that fire after the provider has started, or retry smaller.
3. **Sessions of `chat` got 14% more expensive after prompt `chat:v1` became `chat:v2`**: $6.71 a month. The first call of a session alone got +53%, which points at the prompt itself. Observed over time, not proven causal. Diff the two prompts; a gate on the next change would have caught this.

**Likely: rests on a stated assumption**

4. **A cap at the 99th percentile on the `free` plan would hit 4 users**: $68.97 a month. The cap is $8.33 per user per period. Assumes those users stop rather than leave. Set a per-user budget; see the table of caps for other levels.
5. **82,575 calls re-sent a long identical prompt prefix a provider could have cached**: $44.12 a month. Largest: `research:v1` (2,200 tokens). Assumes the provider's cache rules in the price table. Put the static prompt first and turn on prompt caching.

**Worth testing: quality is not known**

6. **`research_agent` on `gpt-4o-mini` would cost 94% less**: $769 a month. Same tokens, other price. Answer quality is not known. Replay real requests on it and compare answers before switching.
7. **Keeping only the last 4,000 tokens of earlier conversation per call would trim input cost**: $556 a month. Most of it in `research_agent`. Answers may get worse when older context is dropped. Summarize or truncate history and replay real sessions to check quality.
8. **`doc_summary` on `gpt-4o-mini` would cost 94% less**: $76.56 a month. Same tokens, other price. Answer quality is not known. Replay real requests on it and compare answers before switching.

## Who and what costs money

| Feature | Cost | Sessions | Mean / session | p95 / session | Success | Cost per success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `research_agent` | $818 | 1,126 | $0.7264 | $1.09 | 68% | $1.07 ($0.6948 without stuck sessions) |
| `chat` | $104 | 4,617 | $0.0225 | $0.1331 | 88% | $0.0254 |
| `doc_summary` | $81.45 | 1,748 | $0.0466 | $0.1148 | 92% | $0.0508 |

| Model | Cost | Calls |
| --- | ---: | ---: |
| `gpt-4o` | $900 | 14,953 |
| `gpt-4o-mini` | $104 | 71,068 |

Input tokens are 96% of spend. Earlier conversation carried into later calls is about 69% of spend (estimated from consecutive calls in a session).

A session's 10th call carries this many times the input of its 1st: `chat` 5.8x, `research_agent` 20.3x.

## Users

A typical active user costs $0.1405 a month, the 90th percentile $4.21, the 99th $31.23, the heaviest $53.92. The top 1% of users are 25% of spend and the top 10% 78%.

| User | Plan | Cost | x median | Mostly |
| --- | --- | ---: | ---: | --- |
| `u0330` | pro | $53.92 | 384x | `research_agent` |
| `u0326` | pro | $45.48 | 324x | `research_agent` |
| `u0679` | pro | $41.53 | 296x | `research_agent` |
| `u0787` | pro | $41.21 | 293x | `research_agent` |
| `u0713` | free | $35.54 | 253x | `chat` |
| `u0056` | pro | $31.23 | 222x | `research_agent` |
| `u0185` | free | $29.79 | 212x | `chat` |
| `u0277` | free | $28.35 | 202x | `chat` |

## Plans and margin

| Plan | Price | Registered | Active | Cost | Revenue | Margin | Per registered user | Underwater |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| free | $0 | 697 | 483 | $272 | $0 | -$272 | $0.3902 | n/a |
| pro | $20.00 | 103 | 98 | $732 | $2,060 | $1,328 | $7.10 | 6 users |

A free user costs $0.3902 a month and a `pro` user $7.10. To cover the free tier, about 2.9% of signups must pay (plausible range 2.0% to 4.1%); you are at 12.9%.

### If users were capped (assumes they stop at the cap)

| Plan | Cap at | Per-user cap | Users hit | Saved a month |
| --- | ---: | ---: | ---: | ---: |
| free | p90 | $0.9853 | 48 | $148 |
| free | p95 | $1.38 | 24 | $135 |
| free | p99 | $8.33 | 4 | $68.97 |
| pro | p90 | $14.37 | 9 | $159 |
| pro | p95 | $31.23 | 4 | $57.22 |
| pro | p99 | $53.92 | 0 | $0 |

### Margin per 1,000 signups if price or conversion changes

Holds each user's usage fixed and resamples the observed users. The range covers sampling uncertainty only. A higher price may change who signs up and how much they use.

| Paid share of signups | Price | Margin (median, 5th to 95th) |
| --- | ---: | ---: |
| 2.0% | $10.00 | -$318 (-$489 to -$196) |
| 2.0% | $20.00 | -$118 (-$289 to $4.06) |
| 2.0% | $30.00 | $82.35 (-$89.02 to $204) |
| 5.0% | $10.00 | -$225 (-$390 to -$84.63) |
| 5.0% | $20.00 | $275 ($110 to $415) |
| 5.0% | $30.00 | $775 ($610 to $915) |
| 10.0% | $10.00 | -$64.18 (-$258 to $123) |
| 10.0% | $20.00 | $936 ($742 to $1,123) |
| 10.0% | $30.00 | $1,936 ($1,742 to $2,123) |
| 12.9% | $10.00 | $27.54 (-$191 to $254) |
| 12.9% (observed) | $20.00 | $1,315 ($1,097 to $1,541) |
| 12.9% | $30.00 | $2,603 ($2,384 to $2,829) |

## Prompt prefixes a provider could cache (estimated)

| Prefix | Tokens | Calls that could hit | Saved a month |
| --- | ---: | ---: | ---: |
| `research:v1` | 2,200 | 12,214 | $33.60 |
| `chat:v2` | 2,600 | 34,803 | $6.79 |
| `chat:v1` | 1,400 | 35,558 | $3.73 |

## Cost per session before and after a prompt version change (observed)

| Feature | From | To | Per session before | after | Change |
| --- | ---: | ---: | ---: | ---: | ---: |
| `chat` | `chat:v1` | `chat:v2` | $0.0210 | $0.0240 | +14% |

## Stuck agent sessions (worst five)

| Session | User | Identical calls in a row | Session cost | Avoidable |
| --- | ---: | ---: | ---: | ---: |
| `s3725` | `u0465` | 66 | $8.92 | $8.58 |
| `s2796` | `u0348` | 63 | $8.61 | $8.35 |
| `s595` | `u0056` | 65 | $8.87 | $8.18 |
| `s2065` | `u0255` | 63 | $8.35 | $8.04 |
| `s2144` | `u0266` | 65 | $8.84 | $8.04 |

