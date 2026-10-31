"""The OpenAI-compatible gateway (Bifrost) that every model call goes through."""

import os

import httpx

DEFAULT_URL = "http://bifrost:8080"


def url() -> str:
    return os.environ.get("GATEWAY_URL", DEFAULT_URL)


def public_url() -> str:
    """Where a person opens the gateway's own admin page."""
    return os.environ.get("BIFROST_PUBLIC_URL", "http://localhost:8080")


def headers() -> dict:
    key = os.environ.get("BIFROST_VIRTUAL_KEY", "")
    return {"Content-Type": "application/json", **({"x-bf-vk": key} if key else {})}


def client(**options) -> httpx.Client:
    """A client bound to the gateway; callers choose their own timeouts."""
    return httpx.Client(base_url=url(), headers=headers(), **options)


def status() -> dict:
    """Reachability and the model IDs the gateway offers."""
    try:
        # The first listing can be slow while the gateway queries every provider.
        with client(timeout=10) as http:
            response = http.get("/v1/models")
            response.raise_for_status()
            models = response.json().get("data", [])
        return {"status": "connected", "models": [m.get("id") for m in models],
                "settings_url": public_url()}
    except (httpx.HTTPError, ValueError):
        return {"status": "unavailable", "models": [], "settings_url": public_url()}
