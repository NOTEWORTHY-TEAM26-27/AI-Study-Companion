"""Local model adapter; the HTTP app depends only on generate_answer(prompt)."""

import json
import os
import urllib.error
import urllib.request
from http.client import HTTPException


def generate_answer(prompt: str) -> str:
    request = urllib.request.Request(
        os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate"),
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
