#!/bin/bash
# Cost gate on recorded evidence: the same check a pipeline runs before a prompt change ships.
# Recorded support-triage runs (see README.md here): the verbose candidate prompt must be
# blocked (exit 1) and the analyst's fix must pass against the original baseline (exit 0).
# In GitHub Actions the summary goes to the job summary page; locally it prints here.
set -uo pipefail
cd "$(dirname "$0")/../.."
summary="${GITHUB_STEP_SUMMARY:-/dev/stdout}"

gate() {
  docker compose --profile demo run --rm --no-deps -T app python -m costguard gate \
    --baseline examples/ci/baseline.json --candidate "examples/ci/$1.json" \
    --pricing examples/ci/pricing.json --policy examples/ci/policy.json \
    --monthly-requests 100000 --json-output /dev/null --markdown-output /dev/stdout
}

echo "## CostGuard cost gate · recorded support-triage runs" >> "$summary"
gate candidate >> "$summary"; candidate=$?
gate fix >> "$summary"; fix=$?
echo "Verbose candidate prompt: exit $candidate (expected 1, blocked). Analyst's fix: exit $fix (expected 0, passes)."
[ "$candidate" -eq 1 ] && [ "$fix" -eq 0 ]
