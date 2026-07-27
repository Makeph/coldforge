"""ICP builder + lead fit scoring — "drop your site, learn who buys it".

The AutoGTM idea (Explee, Clay, …) reduced to its honest core: read your *own*
product page, derive an Ideal Customer Profile — what you sell, whose pain it
removes, which segments buy — then score every stored lead 0–100 against it so
campaigns start with the best-fit people instead of the whole CSV.

The profile is a plain JSON file (``$COLDFORGE_HOME/icp.json``) you can edit by
hand — the model proposes, you stay in charge. Both steps degrade gracefully:

* **build** — an LLM reads the scraped page when a key is set; otherwise a
  keyword profile is extracted locally.
* **score** — the model judges fit when a key is set; otherwise a deterministic
  keyword-overlap heuristic keeps ranking usable offline.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, get_settings
from .models import Lead
from .research import scrape

_WORD_RE = re.compile(r"[a-zà-ü][a-zà-ü'\-]{3,}", re.IGNORECASE)

# Minimal FR+EN stopwords — just enough to keep the heuristic keywords useful.
_STOP = {
    "avec", "pour", "dans", "vous", "votre", "vos", "nous", "notre", "leur",
    "cette", "sont", "être", "fait", "faire", "tout", "tous", "toute", "plus",
    "sans", "chaque", "entre", "aussi", "comme", "mais", "donc", "alors",
    "this", "that", "with", "your", "from", "have", "will", "they", "them",
    "what", "when", "where", "which", "their", "there", "been", "were", "than",
    "then", "also", "just", "into", "over", "more", "most", "some", "such",
    "very", "each", "every", "about", "after", "before", "because", "while",
}


def icp_path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).home / "icp.json"


def load_icp(settings: Settings | None = None) -> dict | None:
    path = icp_path(settings)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_icp(icp: dict, settings: Settings | None = None) -> Path:
    path = icp_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(icp, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _keywords(text: str, k: int = 24) -> list[str]:
    counts: dict[str, int] = {}
    for w in _WORD_RE.findall(text.lower()):
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:k]]


def _heuristic_icp(site: str, text: str) -> dict:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    product = " ".join(s.strip() for s in sentences[:3] if s.strip())[:400]
    return {
        "site": site,
        "product": product,
        "pains": [],
        "segments": [],
        "keywords": _keywords(text),
        # content/SEO opportunities: needs a model to infer what's *missing*
        # from the page, so the offline heuristic leaves this empty.
        "content_gaps": [],
        "source": "heuristic",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _llm_icp(site: str, text: str, settings: Settings) -> dict | None:
    try:
        import anthropic
    except ImportError:
        return None
    prompt = (
        "Below is the scraped text of a product website. Derive an Ideal Customer "
        "Profile for cold outreach. Answer with ONLY a JSON object, keys:\n"
        '  "product": one-paragraph summary of what is sold (same language as the site),\n'
        '  "pains": list of 3-6 short pains it removes,\n'
        '  "segments": list of {"name", "fit" (0-100 int), "why"} — 3-6 buyer segments '
        "ranked by likelihood to pay,\n"
        '  "keywords": 15-25 lowercase words/phrases likely to appear in a good-fit '
        "lead's company, title or website,\n"
        '  "content_gaps": list of {"topic", "why"} — 4-8 questions or subjects this '
        "company's buyers search for that the page above does NOT visibly answer or "
        "rank for (a content/SEO opportunity — feeds `coldforge content plan`).\n\n"
        f"Site: {site}\n---\n{text[:6000]}"
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else None
    except Exception:  # any failure -> heuristic fallback
        return None
    if not isinstance(data, dict) or "product" not in data:
        return None
    data.update({
        "site": site, "source": "llm",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    data.setdefault("keywords", _keywords(text))
    data.setdefault("content_gaps", [])
    return data


def build_icp(site: str, settings: Settings | None = None) -> dict:
    """Scrape *site* and derive an ICP. Raises ``ValueError`` if the site
    yields no text (wrong URL, blocked, …) — better loud than an empty profile."""
    settings = settings or get_settings()
    text = scrape(site, max_chars=8000)
    if not text:
        raise ValueError(f"Could not read any text from '{site}'.")
    icp = None
    if settings.has_ai:
        icp = _llm_icp(site, text, settings)
    return icp or _heuristic_icp(site, text)


# ── scoring ──────────────────────────────────────────────────────────────────
def _match(needle: str, haystack: str) -> bool:
    """Substring match with a cheap plural fold so 'agences' finds 'agence'."""
    if needle in haystack:
        return True
    return needle.endswith("s") and len(needle) > 4 and needle[:-1] in haystack


def _heuristic_score(lead: Lead, icp: dict, signal_text: str = "") -> tuple[int, str]:
    haystack = " ".join([*lead.as_vars().values(), signal_text]).lower()
    if not haystack.strip():
        return 0, "no lead data to match"
    needles: set[str] = {k.lower() for k in icp.get("keywords", [])}
    for seg in icp.get("segments", []):
        needles.update(_WORD_RE.findall(str(seg.get("name", "")).lower()))
    hits = sorted(n for n in needles if n and _match(n, haystack))
    score = min(100, 12 * len(hits))
    reason = ("matches: " + ", ".join(hits[:6])) if hits else "no keyword overlap with ICP"
    return score, reason


def _icp_digest(icp: dict) -> dict:
    """The three ICP fields worth spending prompt tokens on."""
    return {k: icp[k] for k in ("product", "pains", "segments") if k in icp}


def _llm_score(lead: Lead, icp: dict, signal_text: str,
                  settings: Settings) -> tuple[int, str] | None:
    try:
        import anthropic
    except ImportError:
        return None
    lead_desc = "\n".join(f"- {k}: {v}" for k, v in lead.as_vars().items())
    if signal_text:
        lead_desc += f"\n- researched: {signal_text}"
    prompt = (
        "Score how well this lead fits the Ideal Customer Profile, 0-100 "
        "(100 = exactly the buyer). Answer exactly:\nSCORE: <int>\nREASON: <one line>\n\n"
        f"ICP:\n{json.dumps(_icp_digest(icp), ensure_ascii=False)}\n\n"
        f"Lead:\n{lead_desc}"
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model, max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"SCORE:\s*(\d{1,3}).*?REASON:\s*(.+)", raw, re.DOTALL | re.IGNORECASE)
        if not m:
            return None
        return min(100, int(m.group(1))), m.group(2).strip()[:300]
    except Exception:
        return None


def score_lead(lead: Lead, icp: dict, *, signal_text: str = "",
               settings: Settings | None = None) -> tuple[int, str]:
    """Return ``(score 0-100, reason)`` for *lead* against *icp*."""
    settings = settings or get_settings()
    if settings.has_ai:
        scored = _llm_score(lead, icp, signal_text, settings)
        if scored:
            return scored
    return _heuristic_score(lead, icp, signal_text)
