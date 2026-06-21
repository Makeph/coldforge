"""MCP server exposing coldforge's research + drafting to Claude.

Run it with ``coldforge mcp`` (stdio) and point any MCP client at it. The same
shared core powers the CLI, so a draft written here is identical to one written
on the command line.

Tools
-----
* ``research_prospect(name, company, title?, website?)`` → personalization signals
* ``draft_email(template_id, ...)``                      → a ready-to-review email
* ``list_templates(category?)``                          → the template pack
* ``check_deliverability(domain)``                       → SPF / DKIM / DMARC

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
        variables go in `variables`. Returns subject + body; Claude-personalizes
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

    return mcp


def main() -> None:
    _build_server().run()


if __name__ == "__main__":
    main()
