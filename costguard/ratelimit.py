"""Wait out provider rate limits.

A 429 response means the provider rejected the request before processing it, so it is
re-sent after the provider's Retry-After. This is the only automatic re-send: any other
failure, which may already have been billed, is never replayed.
"""

import re
import time

import httpx

MAX_WAIT_SECONDS = 180
_DURATION = re.compile(r"^(?:(\d+)m)?(\d+(?:\.\d+)?)s$")


def _delay(response: httpx.Response, fallback: float) -> float:
    header = response.headers.get("retry-after", "")
    try:
        return max(float(header), 0.0)
    except ValueError:
        pass
    # Groq and OpenAI also report when the token window resets, e.g. "1m2.5s".
    match = _DURATION.match(response.headers.get("x-ratelimit-reset-tokens", ""))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return fallback


def post(client: httpx.Client, url: str, *, json: dict, timeout: float,
         should_continue=lambda: True, sleep=time.sleep,
         max_wait: float = MAX_WAIT_SECONDS, waits: list | None = None) -> httpx.Response:
    """Each wait in seconds is appended to `waits`, so callers can keep it out of latency."""
    waited, fallback = 0.0, 5.0
    while True:
        response = client.post(url, json=json, timeout=timeout)
        if response.status_code != 429:
            return response
        delay = _delay(response, fallback)
        if waited + delay > max_wait or not should_continue():
            return response
        sleep(delay)
        waited += delay
        if waits is not None:
            waits.append(delay)
        fallback = min(fallback * 2, 60)
