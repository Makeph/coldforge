"""SEO/GEO content planning — turn the ICP's keyword gaps into an article plan.

BabyLoveGrowth's core loop is: cluster keywords → generate articles → track
whether they make you visible on Google and on AI answer engines. coldforge
borrows the honest, editable version of the first two steps (the third is
``coldforge geo check``): read the stored ICP (``coldforge icp build``),
cluster its ``keywords`` / ``content_gaps`` into a numbered list of article
briefs, then draft any one of them.

The plan and its drafts live in one plain JSON file
(``$COLDFORGE_HOME/content/plan.json``) you can edit by hand — same "model
proposes, you stay in charge" philosophy as ``icp.py``. Degrades gracefully:
an LLM writes titles/angles/bodies when ``ANTHROPIC_API_KEY`` is set,
otherwise a deterministic keyword-chunking heuristic keeps planning usable
offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .config import Settings, get_settings
from .models import ContentBrief

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, n: int) -> str:
    return f"{n:02d}-" + _SLUG_RE.sub("-", text.lower()).strip("-")[:40]


def content_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).home / "content"


def plan_path(settings: Settings | None = None) -> Path:
    return content_dir(settings) / "plan.json"


def load_plan(settings: Settings | None = None) -> list[ContentBrief]:
    path = plan_path(settings)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    briefs = []
    for b in data:
        if isinstance(b.get("created_at"), str):
            b = {**b, "created_at": datetime.fromisoformat(b["created_at"])}
        briefs.append(ContentBrief(**b))
    return briefs


def save_plan(briefs: list[ContentBrief], settings: Settings | None = None) -> Path:
    path = plan_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(b) for b in briefs], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def find_brief(brief_id: str, settings: Settings | None = None) -> ContentBrief | None:
    return next((b for b in load_plan(settings) if b.id == brief_id), None)


# ── planning ─────────────────────────────────────────────────────────────────
def _heuristic_plan(icp: dict, count: int) -> list[ContentBrief]:
    gaps = [g for g in icp.get("content_gaps", []) if g.get("topic")]
    keywords = list(icp.get("keywords", []))
    briefs: list[ContentBrief] = []
    for i in range(count):
        chunk = keywords[i * 3:(i + 1) * 3]
        if gaps:
            gap = gaps[i % len(gaps)]
            topic, angle = gap["topic"], gap.get("why", "")
            chunk = chunk or keywords[:3]
        elif chunk:
            topic = f"Guide: {', '.join(chunk)}"
            angle = (
                "Local keyword cluster from the ICP "
                "(no content_gaps — set ANTHROPIC_API_KEY for those)."
            )
        else:
            break
        briefs.append(ContentBrief(id=_slug(topic, i + 1), topic=topic,
                                   keywords=chunk, angle=angle))
    return briefs


def _llm_plan(icp: dict, count: int, settings: Settings) -> list[ContentBrief] | None:
    try:
        import anthropic
    except ImportError:
        return None
    icp_view = {k: icp[k] for k in
                ("product", "pains", "segments", "keywords", "content_gaps") if k in icp}
    prompt = (
        f"Below is an Ideal Customer Profile (ICP) for a company's cold outreach and "
        f"content marketing. Propose exactly {count} SEO/GEO article briefs — content "
        "that would make this company more visible on Google AND on AI answer engines "
        "(ChatGPT, Claude, Perplexity, Gemini) for topics its buyers actually search. "
        "Prioritize the content_gaps if present. Answer with ONLY a JSON array, each item:\n"
        '  {"topic": working title, "keywords": [3-6 target keywords/phrases], '
        '"angle": one sentence on the specific take that differentiates it}\n\n'
        f"ICP:\n{json.dumps(icp_view, ensure_ascii=False)[:6000]}"
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else None
    except Exception:  # any failure -> heuristic fallback
        return None
    if not isinstance(data, list) or not data:
        return None
    return [
        ContentBrief(id=_slug(str(item.get("topic", f"topic-{i}")), i + 1),
                     topic=str(item.get("topic", "")),
                     keywords=[str(k) for k in item.get("keywords", [])],
                     angle=str(item.get("angle", "")))
        for i, item in enumerate(data[:count])
    ]


def plan_content(icp: dict, count: int = 6, settings: Settings | None = None) -> list[ContentBrief]:
    """Cluster the ICP's ``keywords`` / ``content_gaps`` into *count* article briefs."""
    settings = settings or get_settings()
    briefs = None
    if settings.has_ai:
        briefs = _llm_plan(icp, count, settings)
    return briefs or _heuristic_plan(icp, count)


# ── drafting ─────────────────────────────────────────────────────────────────
def _heuristic_draft(brief: ContentBrief, icp: dict) -> str:
    kw = ", ".join(brief.keywords) or "—"
    return (
        f"# {brief.topic}\n\n"
        f"*Angle: {brief.angle or '(add one — why is this take different?)'}*\n"
        f"*Target keywords: {kw}*\n\n"
        "## Outline\n"
        "1. The problem, stated the way the buyer would search for it\n"
        "2. Why the usual advice falls short\n"
        f"3. How {icp.get('site', 'you')} approaches it differently\n"
        "4. A concrete example or number\n"
        "5. What to do this week\n\n"
        f"_Skeleton only — set ANTHROPIC_API_KEY for a full draft "
        f"(`coldforge content draft {brief.id}`)._\n"
    )


def _llm_draft(brief: ContentBrief, icp: dict, settings: Settings) -> str | None:
    try:
        import anthropic
    except ImportError:
        return None
    prompt = (
        "Write an SEO/GEO article in Markdown for the brief below. Optimize it to be "
        "quotable whole by AI answer engines (a direct answer near the top, clear claims, "
        "concrete numbers/examples) as well as to rank on Google. Same language as the "
        "company context below. 500-900 words, H2 sections, no fluff intro.\n\n"
        f"Topic: {brief.topic}\nAngle: {brief.angle}\n"
        f"Target keywords: {', '.join(brief.keywords)}\n\n"
        f"Company context: {(icp.get('product') or '')[:600]}"
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception:
        return None


def draft_article(brief: ContentBrief, icp: dict, settings: Settings | None = None,
                   *, force_heuristic: bool = False) -> ContentBrief:
    """Write the body for *brief* in place (LLM if a key is set, else a skeleton
    outline) and mark it ``drafted``. Returns *brief* for convenience."""
    settings = settings or get_settings()
    body = None
    if settings.has_ai and not force_heuristic:
        body = _llm_draft(brief, icp, settings)
    brief.body = body or _heuristic_draft(brief, icp)
    brief.status = "drafted"
    return brief
