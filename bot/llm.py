"""Talks to the AI model used for hourly check-ins.

Settings come from GitHub Secrets (never stored in the repository):
  LLM_API_KEY   — the API key
  LLM_BASE_URL  — the provider's address, e.g. https://api.anthropic.com or an
                  OpenAI-compatible gateway like https://openrouter.ai/api/v1
  LLM_MODEL     — optional; overrides `ai.checkin_model` in rules.yaml
"""
from __future__ import annotations

import json
import os
import re

import requests


class LLMUnavailable(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"))


def chat(system: str, user: str, model: str, max_tokens: int = 800) -> str:
    key = os.environ.get("LLM_API_KEY")
    base = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
    model = os.environ.get("LLM_MODEL") or model
    if not key or not base:
        raise LLMUnavailable("LLM_API_KEY / LLM_BASE_URL not set")
    try:
        if "anthropic.com" in base:
            r = requests.post(
                f"{base}/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=60,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "temperature": 0,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""
    except requests.RequestException as e:
        raise LLMUnavailable(str(e)) from e
    except (KeyError, IndexError, ValueError) as e:
        raise LLMUnavailable(f"unexpected response: {e}") from e


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)
