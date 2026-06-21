"""Prospect research — turn a name/company/site into a personalization signal.

Provider-agnostic with a zero-key default so the tool works out of the box:

* ``tavily``      — best quality, used when ``TAVILY_API_KEY`` is set.
* ``duckduckgo``  — free HTML endpoint, no key, used as the default fallback.
* ``website``     — scrape the lead's own site and lift a usable sentence.

Each provider returns plain :class:`~coldforge.models.Signal` objects. Network
failures degrade to an empty result rather than raising — research should never
crash a campaign.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote_plus, urlparse

import requests

from .config import Settings, get_settings
from .models import Lead, ResearchResult, Signal

_UA = "Mozilla/5.0 (compatible; coldforge/0.1; +https://github.com/Makeph/coldforge)"
_TIMEOUT = 12
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DDG_RESULT_RE = re.compile(
    r'result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def scrape(url: str, *, max_chars: int = 4000) -> str:
    """Fetch a page and return cleaned, tag-stripped text (best-effort)."""
    if not url:
        return ""
    if not urlparse(url).scheme:
        url = "https://" + url
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return ""
    body = resp.text
    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", body, flags=re.DOTALL | re.I)
    return _clean(body)[:max_chars]


def _first_sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(p.strip() for p in parts[:n] if p.strip())[:280]


def _search_tavily(query: str, key: str, k: int = 4) -> list[Signal]:
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": k,
                  "search_depth": "basic", "include_answer": True},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []
    signals: list[Signal] = []
    if answer := (data.get("answer") or "").strip():
        signals.append(Signal(lead_id=0, text=_clean(answer)[:280], source="tavily"))
    for item in data.get("results", [])[:k]:
        snippet = _clean(item.get("content", ""))
        if snippet:
            signals.append(Signal(lead_id=0, text=snippet[:280], source="tavily",
                                  url=item.get("url", "")))
    return signals


def _search_duckduckgo(query: str, k: int = 4) -> list[Signal]:
    try:
        resp = requests.get(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            headers={"User-Agent": _UA}, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []
    signals: list[Signal] = []
    for m in _DDG_RESULT_RE.finditer(resp.text):
        snippet = _clean(m.group("snippet"))
        if snippet:
            signals.append(Signal(lead_id=0, text=snippet[:280], source="duckduckgo",
                                  url=_clean(m.group("url"))))
        if len(signals) >= k:
            break
    return signals


def research_lead(lead: Lead, settings: Settings | None = None) -> ResearchResult:
    """Gather signals about *lead* using the best available provider.

    Strategy: web search on ``name + company``, then enrich with a scrape of the
    lead's own site if present. The lead id is stamped onto every signal so the
    caller can persist them directly.
    """
    settings = settings or get_settings()
    name = " ".join(p for p in (lead.first_name, lead.last_name) if p).strip()
    query = " ".join(p for p in (name, lead.company, lead.title) if p).strip()

    signals: list[Signal] = []
    if query:
        if settings.tavily_api_key:
            signals = _search_tavily(query, settings.tavily_api_key)
        if not signals:
            signals = _search_duckduckgo(query)

    if lead.website:
        text = scrape(lead.website, max_chars=2500)
        if text:
            signals.append(Signal(lead_id=0, text=_first_sentences(text), source="website",
                                  url=lead.website))

    for s in signals:
        s.lead_id = lead.id or 0

    summary = signals[0].text if signals else ""
    return ResearchResult(signals=signals, summary=summary)
