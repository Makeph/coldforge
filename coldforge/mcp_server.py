"""MCP server exposing coldforge's research + drafting over MCP.

Run it with ``coldforge mcp`` (stdio) and point any MCP client at it. The same
shared core powers the CLI, so a draft written here is identical to one written
on the command line.

Tools
-----
* ``research_prospect(name, company, title?, website?)`` → personalization signals
* ``draft_email(template_id, ...)``                      → a ready-to-review email
* ``list_templates(category?)``                          → the template pack
* ``check_deliverability(domain)``                       → SPF / DKIM / DMARC
* ``build_icp(site)``                                    → who buys what you sell
* ``score_prospect(...)``                                → 0–100 fit vs the ICP
* ``lint_email(subject, body)``                          → spam-filter content check
* ``classify_reply(subject, body)``                      → reply triage category
* ``geo_check_visibility(query, brand)``                 → mentioned by ChatGPT/Claude/Perplexity/Gemini?
* ``plan_content(count?)``                                → ICP keyword gaps → article briefs
* ``draft_article(brief_id)``                             → write one planned article

Requires the ``mcp`` extra: ``pip install 'coldforge[mcp]'``.
"""

from __future__ import annotations

from .config import get_settings
from .models import Lead, Signal
from .personalize import draft_email as _draft_email
from .research import research_lead
from .templates import by_category, get as get_template


def _build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - guarded by CLI message
        raise ImportError("Install the MCP extra: pip install 'coldforge[mcp]'") from exc

    mcp = FastMCP("coldforge")

    @mcp.tool()
    def list_templates(category: str = "") -> list[dict]:
        """List available cold-email templates, optionally filtered by category
        (sales, recruiting, partnership, warm-intro, networking, follow-up)."""
        return [
            {"id": t.id, "category": t.category, "name": t.name,
             "use_case": t.use_case, "variables": sorted(t.required_vars())}
            for t in by_category(category or None)
        ]

    @mcp.tool()
    def research_prospect(name: str, company: str = "", title: str = "",
                          website: str = "") -> dict:
        """Research a prospect and return personalization signals (a concrete,
        true fact you can open a cold email with). Uses Tavily if TAVILY_API_KEY
        is set, otherwise a free DuckDuckGo fallback, plus the prospect's own site."""
        first, _, last = name.partition(" ")
        lead = Lead(email="unknown@example.com", first_name=first, last_name=last,
                    company=company, title=title, website=website)
        result = research_lead(lead, get_settings())
        return {
            "summary": result.summary,
            "signals": [{"text": s.text, "source": s.source, "url": s.url}
                        for s in result.signals],
        }

    @mcp.tool()
    def draft_email(template_id: str, first_name: str = "", company: str = "",
                    title: str = "", signal: str = "", variables: dict | None = None) -> dict:
        """Draft a cold email from a template for one prospect. Pass a `signal`
        (e.g. from research_prospect) to make the opener specific. Extra template
        variables go in `variables`. Returns subject + body; LLM-personalizes
        the body when ANTHROPIC_API_KEY is set, else fills the template."""
        lead = Lead(email="unknown@example.com", first_name=first_name,
                    company=company, title=title, custom=variables or {})
        sig = Signal(lead_id=0, text=signal, source="manual") if signal else None
        d = _draft_email(template_id, lead, signal=sig, extra_vars=variables,
                         settings=get_settings())
        return {"subject": d.subject, "body": d.body, "template_id": d.template_id,
                "personalized": d.personalized, "notes": d.notes}

    @mcp.tool()
    def show_template(template_id: str) -> dict:
        """Return one template's metadata, subject, body and deliverability notes."""
        t = get_template(template_id)
        return {"id": t.id, "name": t.name, "category": t.category, "persona": t.persona,
                "use_case": t.use_case, "deliverability_notes": t.deliverability_notes,
                "subject": t.subject, "body": t.body,
                "variables": sorted(t.required_vars())}

    @mcp.tool()
    def check_deliverability(domain: str) -> dict:
        """Check a sending domain's SPF / DKIM / DMARC and return a 0–100 score."""
        from .deliverability import check_domain

        r = check_domain(domain)
        return {"domain": r.domain, "score": r.score, "verdict": r.verdict,
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, "fix": c.fix}
                           for c in r.checks]}

    @mcp.tool()
    def build_icp(site: str) -> dict:
        """Read a product website and derive an Ideal Customer Profile: what it
        sells, the pains it removes, and ranked buyer segments. Saved to
        $COLDFORGE_HOME/icp.json so `score_prospect` and the CLI can use it."""
        from .icp import build_icp as _build, save_icp

        icp = _build(site, get_settings())
        save_icp(icp)
        return icp

    @mcp.tool()
    def score_prospect(email: str = "", first_name: str = "", company: str = "",
                       title: str = "", website: str = "", signal: str = "") -> dict:
        """Score a prospect 0-100 against the stored ICP (build_icp first).
        Pass whatever fields you know; a researched `signal` sharpens the score."""
        from .icp import load_icp, score_lead

        icp = load_icp()
        if not icp:
            return {"error": "No ICP stored yet — call build_icp(site) first."}
        lead = Lead(email=email or "unknown@example.com", first_name=first_name,
                    company=company, title=title, website=website)
        score, reason = score_lead(lead, icp, signal_text=signal, settings=get_settings())
        return {"score": score, "reason": reason, "icp_site": icp.get("site", "")}

    @mcp.tool()
    def lint_email(subject: str, body: str) -> dict:
        """Spam-filter check email copy before sending: trigger words (EN+FR),
        link count, all-caps, length, unfilled variables. Score < 70 → rewrite."""
        from .lint import lint_draft

        r = lint_draft(subject, body)
        return {"score": r.score, "ok": r.ok,
                "issues": [{"severity": i.severity, "message": i.message} for i in r.issues]}

    @mcp.tool()
    def classify_reply(subject: str, body: str) -> dict:
        """Triage a reply to a cold email: interested, not_interested,
        unsubscribe, ooo, or other. An unsubscribe should go straight to
        `coldforge suppress add`."""
        from .replies import classify_reply as _classify

        return {"category": _classify(subject, body, get_settings())}

    @mcp.tool()
    def geo_check_visibility(query: str, brand: str) -> dict:
        """Ask every configured AI engine (Claude/OpenAI/Perplexity/Gemini,
        whichever have an API key set) a buyer question and check whether
        `brand` is mentioned in the answer — the GEO/AI-search-visibility idea
        behind tools like BabyLoveGrowth, done by asking the engines directly."""
        from .geo import check_visibility

        results = check_visibility(query, brand, get_settings())
        return {
            "query": query, "brand": brand,
            "results": [{"engine": r.engine, "mentioned": r.mentioned, "snippet": r.snippet}
                        for r in results],
        }

    @mcp.tool()
    def plan_content(count: int = 6) -> dict:
        """Cluster the stored ICP's keywords/content_gaps into `count` SEO/GEO
        article briefs (call build_icp first). Saved to
        $COLDFORGE_HOME/content/plan.json; draft one with draft_article."""
        from .content import plan_content as _plan_content
        from .content import save_plan
        from .icp import load_icp

        icp = load_icp()
        if not icp:
            return {"error": "No ICP stored yet — call build_icp(site) first."}
        briefs = _plan_content(icp, count, get_settings())
        save_plan(briefs)
        return {"briefs": [{"id": b.id, "topic": b.topic, "keywords": b.keywords,
                            "angle": b.angle} for b in briefs]}

    @mcp.tool()
    def draft_article(brief_id: str) -> dict:
        """Write one article from a brief planned by plan_content (LLM if
        ANTHROPIC_API_KEY is set, else a skeleton outline)."""
        from .content import draft_article as _draft_article
        from .content import find_brief, load_plan, save_plan
        from .icp import load_icp

        brief = find_brief(brief_id)
        if not brief:
            return {"error": f"Brief '{brief_id}' not found — call plan_content first."}
        _draft_article(brief, load_icp() or {}, get_settings())
        save_plan([brief if b.id == brief.id else b for b in load_plan()])
        return {"id": brief.id, "topic": brief.topic, "body": brief.body, "status": brief.status}

    return mcp


def main() -> None:
    _build_server().run()


if __name__ == "__main__":
    main()
