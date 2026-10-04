#!/bin/bash

set -euo pipefail

echo "Testing CostGuard API..."

for _ in {1..20}; do
  if curl --fail --silent http://localhost:8000/health >/dev/null; then
    break
  fi
  sleep 0.5
done

RESPONSE=$(curl --fail --silent \
  -X POST "http://localhost:8000/compare" \
  -H "Content-Type: application/json" \
  --data @examples/comparison.json)

python -c 'import json, sys; report = json.load(sys.stdin); assert report["policy"]["status"] == "fail"' <<< "$RESPONSE"

echo "CostGuard comparison API is healthy."
