"""Company records shared by discovery, profiling and cohort selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_domain(website: str) -> str:
    """Return a stable lower-case host, without a leading ``www``."""
    value = website.strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


@dataclass
class Company:
    name: str
    domain: str = ""
    website: str = ""
    city: str = ""
    category: str = ""
    phone: str = ""
    source: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    id: int | None = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.website = self.website.strip()
        self.domain = normalize_domain(self.domain or self.website)
