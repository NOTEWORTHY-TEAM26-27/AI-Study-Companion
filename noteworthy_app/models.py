"""Model adapter supporting local Ollama and hosted DigitalOcean inference."""

import json
import os
import urllib.error
import urllib.request
from http.client import HTTPException


def generate_answer(prompt: str) -> str:
    """Generate an answer using the configured AI provider."""
    provider = os.environ.get("AI_PROVIDER", "ollama").strip().lower()

    if provider == "ollama":
        return _generate_ollama(prompt)

    if provider == "digitalocean":
        return _generate_digitalocean(prompt)

    raise RuntimeError(f"Unsupported AI_PROVIDER: {provider}")


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
    api_key = os.environ.get("DIGITALOCEAN_API_KEY")

    if not api_key:
        raise RuntimeError("DIGITALOCEAN_API_KEY is not configured.")

    base_url = os.environ.get(
        "DIGITALOCEAN_BASE_URL",
        "https://inference.do-ai.run/v1",
    ).rstrip("/")

    model = os.environ.get(
        "DIGITALOCEAN_MODEL",
        "anthropic-claude-5.5-haiku",
    )

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

    reasoning_effort = os.environ.get("DIGITALOCEAN_REASONING_EFFORT", "").strip()
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

        choices = payload["choices"]
        answer = choices[0]["message"]["content"]

        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Expected a nonempty answer.")

        if choices[0].get("finish_reason") == "length":
            raise ValueError("The answer was cut off.")

        return answer.strip()

    except urllib.error.HTTPError as exc:
        exc.close()
        raise RuntimeError(f"Hosted answer model is unavailable (HTTP {exc.code}).") from exc
    except (HTTPException, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
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
