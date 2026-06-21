"""Plain dataclasses shared across the core, the CLI and the MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Lead:
    """A single outreach target. ``custom`` carries any extra CSV columns so
    they can be used as template variables (e.g. ``{{role_title}}``)."""

    email: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    title: str = ""
    website: str = ""
    linkedin: str = ""
    custom: dict[str, str] = field(default_factory=dict)
    id: int | None = None

    def as_vars(self) -> dict[str, str]:
        """Flatten into the variable namespace a template can reference."""
        base = {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "company": self.company,
            "title": self.title,
            "website": self.website,
            "linkedin": self.linkedin,
        }
        base.update(self.custom)
        return {k: v for k, v in base.items() if v}


@dataclass
class Signal:
    """A researched fact about a lead used to personalize the opener."""

    lead_id: int
    text: str
    source: str = ""          # "tavily" | "duckduckgo" | "website" | "manual"
    url: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    id: int | None = None


@dataclass
class ResearchResult:
    """Outcome of researching one lead (not necessarily persisted)."""

    signals: list[Signal]
    summary: str = ""

    @property
    def best(self) -> Signal | None:
        return self.signals[0] if self.signals else None


@dataclass
class Draft:
    """A rendered email ready to review or send."""

    subject: str
    body: str
    template_id: str = ""
    personalized: bool = False   # True if an LLM rewrote the body
    notes: str = ""              # deliverability / model notes for the reviewer


@dataclass
class Message:
    """A scheduled (or sent) email inside a campaign sequence."""

    campaign_id: int
    lead_id: int
    step: int
    scheduled_at: datetime
    template_id: str = ""
    subject: str = ""
    body: str = ""
    status: str = "scheduled"      # scheduled | sent | skipped | canceled | failed
    sent_at: datetime | None = None
    variant: str = ""              # A/B label
    error: str = ""
    id: int | None = None


@dataclass
class Campaign:
    name: str
    status: str = "draft"          # draft | active | paused | done
    from_email: str = ""
    sequence: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    id: int | None = None
