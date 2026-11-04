# Deep dive: a unit economics report, built and tested

Follows [PATH.md](PATH.md). That note argued the product should answer "am I losing money on each
user, and what would fix it?" rather than "did this prompt change cost more?". This builds the
smallest real version, `unitecon`, to see whether the idea survives contact with data.

**What was built** (standard library only, about 600 lines): a loader for a call log, a price table,
an analyzer, a Markdown renderer and a CLI. Sample output: [sample-report.md](saas-sim/sample-report.md).

**What it was tested on:** a simulated month for a small AI SaaS ([simulate.py](saas-sim/simulate.py)):
800 users, free and $20 pro plans, three features (chat, document summary, a research agent), 86,000
calls. The simulator plants known problems and writes them to `truth.json`, so the report can be
scored. **All the numbers are synthetic and every parameter is my assumption.** This tests that the
machinery finds real structure; it says nothing about what real apps spend.

## What the report tells a builder

From one file of calls, with no test cases and no baseline:

- **Money by feature, model and user**, with the cost of one *successful* outcome per feature.
- **Waste it can measure**: agent sessions stuck repeating a tool call (30% of simulated spend),
  failed calls that were still billed.
- **Concentration**: the top 1% of users are 25% of spend, the top 10% are 78%.
- **Plan margin**: pro earns $1,328 on $2,060; 6 of 103 pro users cost more than they pay;
  the free tier loses $272.
- **Break-even conversion**: about 2.9% of signups must pay to cover the free tier (range 2.0% to
  4.1%); the simulated app is at 12.9%.
- **A margin grid** for price and conversion, with sampling ranges.
- **Prompt regressions found from the log itself**: chat sessions cost +14% after the prompt grew,
  and the first call of a session alone cost +53%, which points at the prompt.
- **Findings grouped by how well they are known**: measured, estimated, unverified.

## Does it find what was planted?

[validation.txt](saas-sim/validation.txt), full log: all 49 stuck sessions found with no false
ones, all 5 planted heavy users in the top 8, all 2,047 retried calls, and the prompt change.
Then fields were removed one at a time to see what each is worth:

| If the log lacks | What happens |
| --- | --- |
| `session_id` | Rebuilt from time gaps. Stuck sessions still found by count; a script's back-to-back sessions can merge. |
| `tool_sig` (tool name and arguments) | Loops cannot be proven, but an "unusually long session" fallback still flags all 49 as long, without claiming they are loops. |
| `prefix_hash` | Prompt-change and caching findings disappear, with a warning. |
| `feature` | Prompt-change finding is withheld rather than guessed. Before this was fixed it reported a false -49%. |
| `user_id` | Per-user spend, plan margin, caps and break-even are unavailable, and the report says so. |
| everything but model, tokens, time | Totals by model and the model what-if only. |

So the report degrades honestly. It also shows what a capture layer must provide: **user id and
session id** are what unlock most of the value.

## What the build taught

1. **The best findings were about behavior, not edits.** Stuck agent loops, context that grows with
   each call (a session's 10th call carried 20x the input of its 1st for the agent), and a few users
   dominating spend. None of these needs a baseline, and the regression gate would not have caught
   them. That supports the pivot.
2. **A forecast by "more users" is empty.** Scaling the observed mean just multiplies it. The useful
   version moves the business levers: price, paid share of signups, a per-user cap. The first
   version had the empty forecast; the margin grid replaced it.
3. **Dollar ranking puts unverified advice first.** "Move the agent to the small model: 94% cheaper"
   topped the list, which is the generic, quality-blind advice this should not give. Grouping by
   confidence fixed the order and exposed the real job of the existing engine: turning "worth
   testing" into "verified" by replaying real requests.
4. **Heuristics based on percentiles are fragile.** A few heavy users inflate the 95th percentile of
   session length, which hid their long chats from the long-session fallback.
5. **A bug only an ablation found:** with no feature field the prompt-change check compared
   unrelated prompts. Testing with missing data matters as much as testing with full data.

## What a capture layer must do

A logging proxy sees every request, so most fields can be derived rather than asked for:

| Field | Derivable from the request? |
| --- | --- |
| `feature` | Yes: the system prompt's fingerprint, which the user can name once |
| `prefix_hash`, `prefix_tokens` | Yes: hash of the leading identical messages |
| `session_id` | Mostly: a conversation is a request whose messages extend an earlier one |
| `tool_sig` | Yes: tool names and arguments in the assistant messages |
| `latency`, tokens, retries, errors | Yes |
| `user_id` | Only if the app passes it (the OpenAI `user` field or a header) |
| `plan` / price | No: needs a join with the app's own user table |
| outcome (did it work) | No: app-specific; the single hardest signal to get |

Without outcomes there is no cost per success, only cost per request. That is the main reason the
"Act" step needs the replay engine and a scorer rather than the log alone.

## Honest limits

- **Synthetic data.** The planted problems are ones I chose. Finding them proves the code works, not
  that real apps have them, nor that people would change anything. Step 2 of PATH.md (run it on one
  real app and watch whether the owner acts) is still the real test, and the one thing I cannot do.
- **Prices are assumed**, in `unitecon/prices.json`. A real tool needs a maintained, dated table.
- **Estimates are labelled but still estimates**: the cache saving assumes OpenAI-style rules, the
  caps assume users stop rather than leave, the history what-if ignores quality.
- **Not built:** capture, replay verification of the "worth testing" items, a web view, alerting.
  Nothing is connected to the existing `costguard` package yet.

## Suggested next steps

1. **Real data.** Offer the report to two or three people shipping an AI feature and watch what
   they do with it. If it changes nothing for them, stop.
2. **Capture** as a small proxy deriving the fields above, plus a one-line way to attach user id
   and outcome. Without it the report cannot reach real apps.
3. **Verify the "worth testing" findings** by replaying captured requests through the existing
   `costguard` engine and scoring agreement. This is where the old code earns its place.
4. **Guard**: turn the measured findings into limits (step budget, per-user budget) that a proxy
   can enforce, so the report becomes prevention and not only a monthly read.
