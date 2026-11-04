# Simulated builder: a small support bot

A stand-in for what a solo developer would build: a system prompt with a policy document in
context, one order-lookup tool, 8 hand-written tickets, and a fake OpenAI-compatible server so it
runs offline. Used to try CostGuard end to end as a user, not as its author. See ../FINDINGS.md.

```bash
cd explorer/support-bot
python fake_openai.py &                      # fake OpenAI API on :9911
python measure.py                            # writes baseline.json, warm-prompt.json, mini.json
for v in warm-prompt mini; do
  PYTHONPATH=../.. python -m costguard gate --baseline baseline.json --candidate $v.json \
    --pricing pricing.json --policy policy.json --monthly-requests 300000 \
    --json-output /dev/null --markdown-output /dev/stdout
done
```
