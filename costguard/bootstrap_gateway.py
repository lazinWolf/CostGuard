"""Register the deterministic mock provider in a local Bifrost instance."""

import os
import time

import httpx


def bootstrap():
    base = os.environ.get("GATEWAY_URL", "http://bifrost:8080")
    provider = {
        "provider": "costguard-mock",
        "network_config": {"base_url": "http://mock-provider:9000", "max_retries": 0,
                           "default_request_timeout_in_seconds": 10,
                           "allow_private_network": True},
        "custom_provider_config": {"base_provider_type": "openai",
                                   "allowed_requests": {"chat_completion": True}},
    }
    with httpx.Client(timeout=10) as client:
        for _ in range(30):
            try:
                response = client.post(f"{base}/api/providers", json=provider)
                if response.status_code in {200, 201, 409}:
                    break
                if response.status_code < 500:
                    raise RuntimeError(
                        f"Bifrost rejected mock provider ({response.status_code}): {response.text}"
                    )
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("unable to register mock provider in Bifrost")

        existing = client.get(f"{base}/api/providers/costguard-mock/keys")
        existing.raise_for_status()
        names = {key.get("name") for key in existing.json().get("keys", [])}
        for name, model in (("costguard-mock-key", "echo"), ("costguard-analyst-key", "analyst")):
            if name in names:
                continue
            key = client.post(f"{base}/api/providers/costguard-mock/keys", json={
                "name": name, "value": "dummy", "models": [model], "weight": 1.0,
            })
            key.raise_for_status()


if __name__ == "__main__":
    bootstrap()
