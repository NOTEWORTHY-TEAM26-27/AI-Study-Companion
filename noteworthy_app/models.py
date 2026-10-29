"""Model adapter supporting local Ollama and hosted DigitalOcean inference."""

import json
import math
import os
import urllib.error
import urllib.request
from http.client import HTTPException


def generate_answer(prompt: str) -> str:
    """Generate an answer using the configured AI provider."""
    provider = os.environ.get("AI_PROVIDER", "").strip().lower() or "ollama"

    if provider == "ollama":
        return _generate_ollama(prompt)

    if provider == "digitalocean":
        return _generate_digitalocean(prompt)

    # The value is not echoed: two .env lines joined by mistake could put the key in it.
    raise RuntimeError("Unsupported AI_PROVIDER: use ollama or digitalocean.")


def _generate_ollama(prompt: str) -> str:
    """Generate an answer using local Ollama."""
    try:
        request = urllib.request.Request(
            os.environ.get(
                "OLLAMA_URL",
                "http://127.0.0.1:11434/api/generate",
            ),
            data=json.dumps(
                {
                    "model": os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0},
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)

        if not isinstance(payload, dict):
            raise ValueError("Expected a model response object.")

        answer = payload.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Expected a nonempty answer.")

        return answer.strip()

    except urllib.error.HTTPError as exc:
        exc.close()
        raise RuntimeError("Local answer model is unavailable.") from exc
    except (HTTPException, OSError, ValueError) as exc:
        raise RuntimeError("Local answer model is unavailable.") from exc


def _generate_digitalocean(prompt: str) -> str:
    """Generate an answer using DigitalOcean's OpenAI-compatible API."""
    # Strip so a .env saved with Windows line endings still gives a valid header.
    api_key = os.environ.get("DIGITALOCEAN_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("DIGITALOCEAN_API_KEY is not configured.")

    base_url = (
        os.environ.get("DIGITALOCEAN_BASE_URL", "").strip() or "https://inference.do-ai.run/v1"
    ).rstrip("/")

    model = os.environ.get("DIGITALOCEAN_MODEL", "").strip() or "openai-gpt-oss-120b"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_completion_tokens": _max_tokens(),
    }

    # Newer Claude models reject temperature, so it is sent only when set.
    temperature = os.environ.get("DIGITALOCEAN_TEMPERATURE", "").strip()
    if temperature:
        try:
            body["temperature"] = float(temperature)
        except ValueError as exc:
            raise RuntimeError("DIGITALOCEAN_TEMPERATURE must be a number.") from exc
        if not math.isfinite(body["temperature"]):
            raise RuntimeError("DIGITALOCEAN_TEMPERATURE must be a number.")

    # gpt-oss counts its reasoning against the reply limit, so it reasons briefly unless set.
    reasoning_effort = os.environ.get("DIGITALOCEAN_REASONING_EFFORT", "").strip()
    if not reasoning_effort and model.startswith("openai-gpt-oss"):
        reasoning_effort = "low"
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    try:
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)

        choice = payload["choices"][0]

        # Checked first: gpt-oss can spend the whole limit on reasoning and return no text.
        if choice.get("finish_reason") == "length":
            raise RuntimeError("Hosted answer was cut off by the reply limit.")

        answer = choice["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Expected a nonempty answer.")

        return answer.strip()

    except urllib.error.HTTPError as exc:
        exc.close()
        raise RuntimeError(f"Hosted answer model is unavailable (HTTP {exc.code}).") from exc
    except (
        HTTPException,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        RecursionError,
    ) as exc:
        raise RuntimeError("Hosted answer model is unavailable.") from exc


def _max_tokens() -> int:
    """Return the longest hosted reply allowed, in tokens."""
    value = os.environ.get("DIGITALOCEAN_MAX_TOKENS", "").strip() or "4096"

    try:
        max_tokens = int(value)
    except ValueError as exc:
        raise RuntimeError("DIGITALOCEAN_MAX_TOKENS must be a whole number.") from exc

    if max_tokens < 1:
        raise RuntimeError("DIGITALOCEAN_MAX_TOKENS must be at least 1.")

    return max_tokens
