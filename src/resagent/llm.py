"""Minimal shared LLM transport for the conversation layer.

Same conventions as Planner._call_llm_raw (httpx, OpenAI-compatible,
system+user messages, plain-text JSON responses). Planner keeps its own
transport to avoid regression risk; unify later.
"""

from __future__ import annotations

import os


def call_chat(
    system: str,
    user: str,
    *,
    model: str = "deepseek-v4-pro",
    api_base: str = "https://api.deepseek.com",
    api_key_env: str = "DEEPSEEK_API_KEY",
    temperature: float = 0.3,
    max_tokens: int = 4000,
) -> str:
    import httpx

    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError(f"API key not found. Set {api_key_env} env var.")

    client = httpx.Client(timeout=120, trust_env=False)
    resp = client.post(
        f"{api_base}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
