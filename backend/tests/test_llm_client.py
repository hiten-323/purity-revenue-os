"""Shared LLM provider contract tests; no live network and no secrets."""
from __future__ import annotations

from app.services import llm_client


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    last_request = None

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None):
        FakeClient.last_request = {
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": self.timeout,
        }
        return FakeResponse(
            {
                "choices": [
                    {"message": {"content": "NEMOTRON_DRY_RUN_OK"}}
                ]
            }
        )


def test_nvidia_request_contract_and_provenance(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-only")
    monkeypatch.setattr(llm_client.httpx, "Client", FakeClient)

    content, source = llm_client.complete(
        "Return exactly NEMOTRON_DRY_RUN_OK",
        nvidia_timeout=7,
    )

    assert content == "NEMOTRON_DRY_RUN_OK"
    assert source == "nvidia"

    request = FakeClient.last_request
    assert request["url"] == llm_client.NVIDIA_URL
    assert request["headers"]["Authorization"] == "Bearer nvapi-test-only"
    assert request["json"]["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert request["json"]["messages"][0]["content"] == "Return exactly NEMOTRON_DRY_RUN_OK"
    assert request["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["json"]["max_tokens"] == 1024
    assert request["json"]["stream"] is False
    assert request["timeout"] == 7


def test_nvidia_is_first_provider(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-only")
    monkeypatch.setattr(llm_client.httpx, "Client", FakeClient)

    content, source = llm_client.complete("dry run")

    assert content == "NEMOTRON_DRY_RUN_OK"
    assert source == "nvidia"
    assert FakeClient.last_request["url"] == llm_client.NVIDIA_URL


def test_missing_nvidia_key_falls_back_without_network_to_nvidia(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "your_cerebras_api_key_here")
    monkeypatch.setattr(llm_client.httpx, "Client", FakeClient)

    content, source = llm_client.complete("dry run")

    # Ollama is not mocked, so a real NVIDIA request cannot accidentally occur.
    assert source in {"ollama", "none"}
    assert content != "NEMOTRON_DRY_RUN_OK"
