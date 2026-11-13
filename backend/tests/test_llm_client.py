"""Shared LLM provider contract tests; no live network and no secrets."""
from __future__ import annotations

import httpx
import pytest

from app.services import llm_client


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://x")
            raise httpx.HTTPStatusError("err", request=req, response=self)
        return None

    def json(self):
        return self._payload


def make_client(behaviour):
    """behaviour(url, json) -> FakeResponse or raises."""
    class FakeClient:
        requests = []

        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            FakeClient.requests.append({"url": url, "headers": headers, "json": json,
                                        "timeout": self.timeout})
            return behaviour(url, json)
    return FakeClient


OK = lambda url, body: FakeResponse({"choices": [{"message": {"content": "DRY_RUN_OK"}}]})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm_client._COOLDOWN_UNTIL.clear()
    for k in ("NVIDIA_MODELS", "NVIDIA_MODEL", "NVIDIA_TIMEOUT_S", "NVIDIA_TOTAL_BUDGET_S",
              "NVIDIA_REASONING_EFFORT", "NVIDIA_MAX_TOKENS", "CEREBRAS_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-only")
    yield
    llm_client._COOLDOWN_UNTIL.clear()


def test_nvidia_request_contract_and_provenance(monkeypatch):
    C = make_client(OK)
    monkeypatch.setattr(llm_client.httpx, "Client", C)
    content, source = llm_client.complete("Return exactly DRY_RUN_OK", nvidia_timeout=7)
    assert (content, source) == ("DRY_RUN_OK", "nvidia")
    req = C.requests[0]
    assert req["url"] == llm_client.NVIDIA_URL
    assert req["headers"]["Authorization"] == "Bearer nvapi-test-only"
    assert req["json"]["model"] == "google/gemma-4-31b-it"
    assert req["json"]["messages"][0]["content"] == "Return exactly DRY_RUN_OK"
    assert req["json"]["max_tokens"] == 2048
    assert req["json"]["stream"] is False
    assert "chat_template_kwargs" not in req["json"]
    assert req["timeout"] == 7
    assert llm_client.last_model() == "google/gemma-4-31b-it"


def test_reasoning_models_get_their_controls(monkeypatch):
    monkeypatch.setenv("NVIDIA_MODELS", "openai/gpt-oss-20b")
    C = make_client(OK)
    monkeypatch.setattr(llm_client.httpx, "Client", C)
    llm_client.complete("x")
    assert C.requests[0]["json"]["reasoning_effort"] == "low"
    monkeypatch.setenv("NVIDIA_MODELS", "nvidia/nemotron-3.5-lightning-30b-a3b")
    llm_client.complete("x")
    assert C.requests[1]["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_legacy_nvidia_model_env_goes_first(monkeypatch):
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-20b")
    assert llm_client.nvidia_models()[0] == "openai/gpt-oss-20b"
    assert len(set(llm_client.nvidia_models())) == len(llm_client.nvidia_models())


def test_timeout_falls_through_to_next_nvidia_model_and_cools_it(monkeypatch):
    def beh(url, body):
        if body["model"] == "google/gemma-4-31b-it":
            raise httpx.ReadTimeout("slow")
        return OK(url, body)
    C = make_client(beh)
    monkeypatch.setattr(llm_client.httpx, "Client", C)
    content, source = llm_client.complete("x")
    assert (content, source) == ("DRY_RUN_OK", "nvidia")
    assert [r["json"]["model"] for r in C.requests] == ["google/gemma-4-31b-it", "openai/gpt-oss-20b"]
    assert C.requests[0]["timeout"] == 25.0
    # next call skips the cooling model entirely
    C.requests.clear()
    llm_client.complete("y")
    assert [r["json"]["model"] for r in C.requests] == ["openai/gpt-oss-20b"]


def test_reasoning_only_answer_is_a_failure(monkeypatch):
    def beh(url, body):
        if body["model"] == "google/gemma-4-31b-it":
            return FakeResponse({"choices": [{"message": {"content": "", "reasoning_content": "hmm"}}]})
        return OK(url, body)
    monkeypatch.setattr(llm_client.httpx, "Client", make_client(beh))
    assert llm_client.complete("x") == ("DRY_RUN_OK", "nvidia")
    assert llm_client.last_model() == "openai/gpt-oss-20b"


def test_cerebras_disabled_by_env_is_never_called(monkeypatch):
    monkeypatch.setenv("CEREBRAS_ENABLED", "0")
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test")
    def beh(url, body):
        if url == llm_client.CEREBRAS_URL:
            raise AssertionError("cerebras must not be called")
        if url == llm_client.NVIDIA_URL:
            return FakeResponse({"error": "busy"}, status=503)
        raise httpx.ConnectError("no ollama")
    monkeypatch.setattr(llm_client.httpx, "Client", make_client(beh))
    assert llm_client.complete("x") == (None, "none")


def test_cerebras_402_disables_it(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    calls = []
    def beh(url, body):
        calls.append(url)
        if url == llm_client.CEREBRAS_URL:
            return FakeResponse({"error": "pay"}, status=402)
        raise httpx.ConnectError("no ollama")
    monkeypatch.setattr(llm_client.httpx, "Client", make_client(beh))
    llm_client.complete("x")
    llm_client.complete("x")
    assert calls.count(llm_client.CEREBRAS_URL) == 1


def test_total_budget_bounds_the_nvidia_phase(monkeypatch):
    monkeypatch.setenv("NVIDIA_TOTAL_BUDGET_S", "2")
    C = make_client(OK)
    monkeypatch.setattr(llm_client.httpx, "Client", C)
    # budget < 3s left -> no NVIDIA attempt; falls through (ollama not reachable)
    def beh(url, body):
        if url == llm_client.NVIDIA_URL:
            raise AssertionError("no nvidia call expected")
        raise httpx.ConnectError("no ollama")
    monkeypatch.setattr(llm_client.httpx, "Client", make_client(beh))
    assert llm_client.complete("x") == (None, "none")


def test_missing_nvidia_key_falls_back_without_network_to_nvidia(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "your_cerebras_api_key_here")
    def beh(url, body):
        assert url == llm_client.OLLAMA_URL
        raise httpx.ConnectError("no ollama")
    monkeypatch.setattr(llm_client.httpx, "Client", make_client(beh))
    content, source = llm_client.complete("dry run")
    assert source == "none" and content is None
