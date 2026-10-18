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
    request = urllib.request.Request(
        os.environ.get(
            "OLLAMA_URL",
            "http://127.0.0.1:11434/api/generate",
        ),
        data=json.dumps(
            {
                "model": os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b"),
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
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
        "llama-4-maverick",
    )

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": 0,
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)

        choices = payload["choices"]
        answer = choices[0]["message"]["content"]

        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Expected a nonempty answer.")

        return answer.strip()

    except urllib.error.HTTPError as exc:
        exc.close()
        raise RuntimeError("Hosted answer model is unavailable.") from exc
    except (HTTPException, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Hosted answer model is unavailable.") from exc
