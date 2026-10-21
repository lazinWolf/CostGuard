"""Register local providers in a Bifrost instance: the mock provider, or Ollama.

    python -m costguard.bootstrap_gateway           # deterministic mock provider (demo/CI)
    python -m costguard.bootstrap_gateway ollama    # Ollama from `--profile local`
"""

import os
import sys
import time

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
# Local inference on CPU can take minutes for a long analyst turn.
OLLAMA_TIMEOUT_SECONDS = 300


def _register(client: httpx.Client, base: str, provider: dict) -> None:
    for _ in range(30):
        try:
            response = client.post(f"{base}/api/providers", json=provider)
            if response.status_code in {200, 201, 409}:
                return
            if response.status_code < 500:
                raise RuntimeError(
                    f"Bifrost rejected provider {provider['provider']} ({response.status_code}): {response.text}"
                )
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"unable to register {provider['provider']} in Bifrost")


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
        _register(client, base, provider)
        existing = client.get(f"{base}/api/providers/costguard-mock/keys")
        existing.raise_for_status()
        names = {key.get("name") for key in existing.json().get("keys") or []}
        for name, model in (("costguard-mock-key", "echo"), ("costguard-analyst-key", "analyst")):
            if name in names:
                continue
            key = client.post(f"{base}/api/providers/costguard-mock/keys", json={
                "name": name, "value": "dummy", "models": [model], "weight": 1.0,
            })
            key.raise_for_status()


def bootstrap_ollama() -> list[str]:
    """Register Ollama and its pulled models, or raise an existing provider's short timeout."""
    base = os.environ.get("GATEWAY_URL", "http://bifrost:8080")
    with httpx.Client(timeout=10) as client:
        tags = client.get(f"{OLLAMA_URL}/api/tags")
        tags.raise_for_status()
        models = sorted(model["name"] for model in tags.json().get("models", []))
        current = client.get(f"{base}/api/providers/ollama")
        if current.status_code == 200:
            config = current.json()
            network = config.get("network_config") or {}
            timeout = network.get("default_request_timeout_in_seconds") or 0
            if timeout >= OLLAMA_TIMEOUT_SECONDS:
                print(f"Bifrost already has an ollama provider (request timeout {timeout}s); left unchanged.")
                return models
            network["default_request_timeout_in_seconds"] = OLLAMA_TIMEOUT_SECONDS
            update = {key: config.get(key) for key in (
                "concurrency_and_buffer_size", "proxy_config", "send_back_raw_request", "send_back_raw_response")}
            client.put(f"{base}/api/providers/ollama",
                       json={**update, "network_config": network}).raise_for_status()
            print(f"Raised the existing ollama provider's request timeout from {timeout}s to "
                  f"{OLLAMA_TIMEOUT_SECONDS}s; other settings and keys are unchanged.")
            return models
        _register(client, base, {
            "provider": "ollama",
            "network_config": {"base_url": OLLAMA_URL, "max_retries": 0,
                               "default_request_timeout_in_seconds": OLLAMA_TIMEOUT_SECONDS,
                               "allow_private_network": True},
            "concurrency_and_buffer_size": {"concurrency": 1, "buffer_size": 10},
        })
        key = client.post(f"{base}/api/providers/ollama/keys", json={
            "name": "ollama-local", "value": "", "models": models, "weight": 1.0,
            "ollama_key_config": {"url": OLLAMA_URL},
        })
        key.raise_for_status()
        print(f"Registered ollama in Bifrost with {len(models)} model(s): {', '.join(models) or 'none pulled yet'}")
        return models


if __name__ == "__main__":
    bootstrap_ollama() if sys.argv[1:] == ["ollama"] else bootstrap()
