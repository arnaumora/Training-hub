"""AI providers behind one interface (roadmap step 8): Claude (Anthropic Messages API) and Gemini
(generateContent). Plain HTTPS with httpx: no SDKs, so the image stays small and tests can swap the transport.

complete(provider, model, system, user, max_tokens) -> Completion(text, input_tokens, output_tokens, model)
  raises NotConfigured  the provider's API key is not set (ANTHROPIC_API_KEY, GEMINI_API_KEY in .env)
         Temporary      worth retrying later: timeouts, connection errors, 429, 5xx, overloaded (a busy
                        answer is first tried again here, after 5 and 20 s)
         Failed         not worth retrying: bad request, bad key, refused, empty answer

Keys are read from the environment on each call and never logged. Output tokens include Gemini's thinking
tokens, since they are billed as output.
"""

import os
import time
from dataclasses import dataclass

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 120.0
KEYS = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
BUSY = (500, 502, 503, 504, 529)  # overloaded or briefly down (529: Anthropic's "overloaded")
QUICK_RETRY_S = (5, 20)

# Tests replace this, so they do not wait.
sleep = time.sleep

# Tests replace this with an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class NotConfigured(Exception):
    pass


class Temporary(Exception):
    pass


class Failed(Exception):
    pass


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


def configured(provider: str) -> bool:
    return bool(os.environ.get(KEYS[provider], "").strip())


def _key(provider: str) -> str:
    key = os.environ.get(KEYS[provider], "").strip()
    if not key:
        raise NotConfigured(f"{KEYS[provider]} is not set")
    return key


def _message(r: httpx.Response) -> str:
    """The provider's own error message, if the body has one (both APIs use {"error": {"message": ...}})."""
    try:
        err = r.json().get("error")
    except ValueError:
        return ""
    msg = err.get("message") if isinstance(err, dict) else None
    return f": {msg[:200]}" if msg else ""


def _post(url: str, headers: dict, body: dict) -> dict:
    # "Busy" answers (overloaded, 5xx) usually clear within seconds: tried again right here after
    # QUICK_RETRY_S before giving up to the queue's slower retries. Not 429 (quota): retrying at once
    # only spends more of it.
    for wait in (*QUICK_RETRY_S, None):
        try:
            with httpx.Client(timeout=TIMEOUT_S, transport=transport) as client:
                r = client.post(url, headers=headers, json=body)
        except httpx.TransportError as e:  # timeouts and connection errors
            raise Temporary(f"{type(e).__name__}") from None
        if r.status_code in BUSY and wait is not None:
            sleep(wait)
            continue
        break
    if r.status_code == 429 or r.status_code in BUSY or r.status_code >= 500:
        raise Temporary(f"HTTP {r.status_code}{_message(r)}")
    try:
        data = r.json()
    except ValueError:
        raise Failed(f"HTTP {r.status_code}, not JSON") from None
    if r.status_code != 200:
        raise Failed(f"HTTP {r.status_code}{_message(r)}")
    return data


def _claude(model: str, system: str, user: str, max_tokens: int) -> Completion:
    data = _post(
        ANTHROPIC_URL,
        {"x-api-key": _key("claude"), "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
        {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
    )
    if data.get("stop_reason") == "refusal":
        raise Failed("the model refused")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise Failed(f"empty answer (stop reason {data.get('stop_reason')})")
    usage = data.get("usage", {})
    return Completion(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), data.get("model", model))


def _gemini(model: str, system: str, user: str, max_tokens: int) -> Completion:
    data = _post(
        GEMINI_URL.format(model=model),
        {"x-goog-api-key": _key("gemini"), "content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
    )
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise Failed(f"no answer{f' (blocked: {reason})' if reason else ''}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise Failed(f"empty answer (finish reason {candidates[0].get('finishReason')})")
    usage = data.get("usageMetadata", {})
    out = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
    return Completion(text, int(usage.get("promptTokenCount", 0)), out, data.get("modelVersion", model))


def complete(provider: str, model: str, system: str, user: str, max_tokens: int = 8192) -> Completion:
    if provider == "claude":
        return _claude(model, system, user, max_tokens)
    if provider == "gemini":
        return _gemini(model, system, user, max_tokens)
    raise Failed(f"unknown provider {provider}")
