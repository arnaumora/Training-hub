"""The provider adapter (app/ai_providers.py) against fake HTTP responses: no network, no real keys."""

import json

import httpx
import pytest

from app import ai_providers as ap


@pytest.fixture()
def fake(monkeypatch):
    """Answer every request with `reply` (status, body) and keep the requests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-claude")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    state = {"reply": (200, {}), "requests": []}

    def handler(request: httpx.Request):
        state["requests"].append(request)
        reply = state["reply"]
        status, body = reply.pop(0) if isinstance(reply, list) else reply
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body) if not isinstance(body, str) else httpx.Response(status, text=body)

    monkeypatch.setattr(ap, "transport", httpx.MockTransport(handler))
    state["slept"] = []
    monkeypatch.setattr(ap, "sleep", state["slept"].append)
    return state


CLAUDE_OK = {
    "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "## Summary\nFine run."}],
    "usage": {"input_tokens": 5120, "output_tokens": 410},
}
GEMINI_OK = {
    "modelVersion": "gemini-3.8-flash",
    "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": "## Summary\nFine run."}]}}],
    "usageMetadata": {"promptTokenCount": 4800, "candidatesTokenCount": 380, "thoughtsTokenCount": 900},
}


def test_claude(fake):
    fake["reply"] = (200, CLAUDE_OK)
    out = ap.complete("claude", "claude-sonnet-5-5", "system text", "user text", 1000)
    assert (out.text, out.input_tokens, out.output_tokens, out.model) == ("## Summary\nFine run.", 5120, 410, "claude-sonnet-5-5")
    req = fake["requests"][0]
    assert str(req.url) == ap.ANTHROPIC_URL
    assert req.headers["x-api-key"] == "sk-test-claude" and req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body == {"model": "claude-sonnet-5-5", "max_tokens": 1000, "system": "system text",
                    "messages": [{"role": "user", "content": "user text"}]}


def test_gemini_skips_thoughts_and_bills_them_as_output(fake):
    fake["reply"] = (200, GEMINI_OK)
    out = ap.complete("gemini", "gemini-3.8-flash", "system text", "user text", 1000)
    assert (out.text, out.input_tokens, out.output_tokens) == ("## Summary\nFine run.", 4800, 1280)
    req = fake["requests"][0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert req.headers["x-goog-api-key"] == "test-gemini" and "key=" not in str(req.url)  # never in the URL
    body = json.loads(req.content)
    assert body["systemInstruction"] == {"parts": [{"text": "system text"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "user text"}]}]
    assert body["generationConfig"] == {"maxOutputTokens": 1000}


@pytest.mark.parametrize("status", [500, 503, 529])
def test_busy_is_tried_again_at_once_then_temporary(fake, status):
    fake["reply"] = (status, {"error": {"message": "The model is overloaded. Please try again later."}})
    with pytest.raises(ap.Temporary, match=f"HTTP {status}: The model is overloaded"):
        ap.complete("gemini", "m", "s", "u")
    assert len(fake["requests"]) == 3 and fake["slept"] == [5, 20]


def test_busy_then_fine(fake):
    fake["reply"] = [(503, {"error": {"message": "overloaded"}}), (200, GEMINI_OK)]
    assert ap.complete("gemini", "m", "s", "u").text == "## Summary\nFine run."
    assert len(fake["requests"]) == 2 and fake["slept"] == [5]


def test_quota_is_temporary_without_trying_again_at_once(fake):
    fake["reply"] = (429, {"error": {"message": "Resource has been exhausted"}})
    with pytest.raises(ap.Temporary, match="HTTP 429: Resource has been exhausted"):
        ap.complete("gemini", "m", "s", "u")
    assert len(fake["requests"]) == 1 and fake["slept"] == []


def test_network_trouble_is_temporary(fake):
    fake["reply"] = (200, httpx.ConnectTimeout("timed out"))
    with pytest.raises(ap.Temporary):
        ap.complete("gemini", "m", "s", "u")


def test_bad_request_fails_with_the_message(fake):
    fake["reply"] = (400, {"error": {"type": "invalid_request_error", "message": "model: not found"}})
    with pytest.raises(ap.Failed, match="HTTP 400: model: not found"):
        ap.complete("claude", "nope", "s", "u")


def test_refusal_and_empty_answers_fail(fake):
    fake["reply"] = (200, {**CLAUDE_OK, "stop_reason": "refusal", "content": []})
    with pytest.raises(ap.Failed, match="refused"):
        ap.complete("claude", "m", "s", "u")
    fake["reply"] = (200, {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ap.Failed, match="blocked: SAFETY"):
        ap.complete("gemini", "m", "s", "u")


def test_no_key(monkeypatch, fake):
    monkeypatch.setenv("GEMINI_API_KEY", " ")
    assert ap.configured("claude") and not ap.configured("gemini")
    with pytest.raises(ap.NotConfigured, match="GEMINI_API_KEY"):
        ap.complete("gemini", "m", "s", "u")
    assert fake["requests"] == []  # nothing was sent
