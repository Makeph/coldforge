"""GEO — Generative Engine Optimization visibility checks.

Cold outreach and content marketing both hinge on one question: when a buyer
asks an AI answer engine about your category, do you show up? BabyLoveGrowth
and similar tools track this across ChatGPT / Claude / Perplexity / Gemini and
sell it as a dashboard; coldforge reduces it to its honest core — ask each
engine the buyer's question yourself and read the answer.

Every engine is optional and independent: configure whichever API keys you
have (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``PERPLEXITY_API_KEY``,
``GEMINI_API_KEY``) and :func:`check_visibility` queries only those. With none
configured it returns an empty list rather than raising — a visibility check
should never crash a research run.
"""

from __future__ import annotations

import re

import requests

from .config import Settings, get_settings
from .models import GeoCheck

_TIMEOUT = 20


def _mentions(text: str, brand: str) -> bool:
    if not text or not brand:
        return False
    return brand.strip().lower() in text.lower()


def _snippet(text: str, brand: str, *, width: int = 220) -> str:
    """The sentence mentioning *brand*, or the answer's first sentence otherwise."""
    if brand:
        for sent in re.split(r"(?<=[.!?])\s+", text):
            if brand.lower() in sent.lower():
                return sent.strip()[:width]
    first = re.split(r"(?<=[.!?])\s+", text.strip())
    return (first[0].strip() if first else text.strip())[:width]


def _ask_claude(query: str, settings: Settings) -> str | None:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model, max_tokens=500,
            messages=[{"role": "user", "content": query}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception:  # noqa: BLE001 — a failed engine shouldn't sink the others
        return None


def _ask_openai(query: str, settings: Settings) -> str | None:
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": settings.openai_model,
                  "messages": [{"role": "user", "content": query}], "max_tokens": 500},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _ask_perplexity(query: str, settings: Settings) -> str | None:
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {settings.perplexity_api_key}"},
            json={"model": settings.perplexity_model,
                  "messages": [{"role": "user", "content": query}]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _ask_gemini(query: str, settings: Settings) -> str | None:
    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}",
            json={"contents": [{"parts": [{"text": query}]}]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


# engine name -> (is configured?, ask function)
_ENGINES: dict[str, tuple] = {
    "claude": (lambda s: bool(s.anthropic_api_key), _ask_claude),
    "openai": (lambda s: bool(s.openai_api_key), _ask_openai),
    "perplexity": (lambda s: bool(s.perplexity_api_key), _ask_perplexity),
    "gemini": (lambda s: bool(s.gemini_api_key), _ask_gemini),
}


def check_visibility(query: str, brand: str, settings: Settings | None = None) -> list[GeoCheck]:
    """Ask *query* (a natural buyer question, e.g. "best CRM for real estate
    agents") to every configured AI engine and check whether *brand* appears in
    the answer.

    Returns one :class:`~coldforge.models.GeoCheck` per engine that answered;
    engines without an API key, or whose call fails, are silently skipped —
    with zero keys set this returns ``[]``.
    """
    settings = settings or get_settings()
    results: list[GeoCheck] = []
    for engine, (configured, ask) in _ENGINES.items():
        if not configured(settings):
            continue
        answer = ask(query, settings)
        if answer is None:
            continue
        results.append(GeoCheck(
            engine=engine, query=query, mentioned=_mentions(answer, brand),
            snippet=_snippet(answer, brand), answer=answer.strip()[:1000],
        ))
    return results
