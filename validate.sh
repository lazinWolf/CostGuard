#!/bin/bash

echo "Testing CostGuard API..."

# Send POST request using curl, silence the output (-s), and extract only the HTTP status code (-w)
STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8000/estimate" \
  -H "Content-Type: application/json" \
  -d '{"prompt_tokens": 500, "completion_tokens": 1000, "model": "gpt-4"}')

if [ "$STATUS_CODE" -eq 200 ]; then
  echo "✅ Success! API returned 200 OK."
  exit 0
else
  echo "❌ Failure! API returned status code: $STATUS_CODE"
  exit 1
fi
