"""Configuration and environment loading.

A tiny, dependency-free `.env` reader (so the package works before anyone runs
``pip install python-dotenv``) plus a typed :class:`Settings` snapshot used
across the CLI, the sending engine and the MCP server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}
_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def load_env(start: Path | None = None) -> None:
    """Load ``.env`` from *start* (or cwd) upward into ``os.environ``.

    Existing environment variables always win — we never clobber a value the
    user exported on purpose. Lines are ``KEY=VALUE``; ``#`` comments and blank
    lines are ignored; surrounding quotes are stripped.
    """
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            _parse_env_file(candidate)
            return


def _parse_env_file(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_window(spec: str) -> tuple[time, time]:
    try:
        start_s, _, end_s = spec.partition("-")
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        return time(sh, sm), time(eh, em)
    except Exception:
        return time(9, 0), time(17, 0)


def _parse_days(spec: str) -> set[int]:
    """``mon-fri`` or ``mon,wed,fri`` → set of weekday indices (Mon=0)."""
    spec = spec.strip().lower()
    if not spec:
        return {0, 1, 2, 3, 4}
    if "-" in spec and "," not in spec:
        a, _, b = spec.partition("-")
        if a in _DAY_INDEX and b in _DAY_INDEX:
            lo, hi = _DAY_INDEX[a], _DAY_INDEX[b]
            return {d % 7 for d in range(lo, hi + 1)} if lo <= hi else set()
    return {_DAY_INDEX[d.strip()] for d in spec.split(",") if d.strip() in _DAY_INDEX}


@dataclass(frozen=True)
class Settings:
    # storage
    home: Path
    db_path: Path
    # personalization
    anthropic_api_key: str | None
    model: str
    # research
    tavily_api_key: str | None
    # identity
    from_name: str
    from_email: str
    # smtp
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_starttls: bool
    # imap
    imap_host: str | None
    imap_port: int
    imap_user: str | None
    imap_password: str | None
    # guardrails
    daily_limit: int
    send_window: tuple[time, time]
    send_days: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})
    min_gap_seconds: int = 45

    @property
    def can_send(self) -> bool:
        return bool(self.smtp_host and self.from_email)

    @property
    def can_detect_replies(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)

    @property
    def has_ai(self) -> bool:
        return bool(self.anthropic_api_key)


def get_settings() -> Settings:
    """Build a :class:`Settings` snapshot from the current environment."""
    load_env()

    home_raw = os.environ.get("COLDFORGE_HOME", "").strip()
    home = Path(home_raw).expanduser() if home_raw else Path.home() / ".coldforge"

    return Settings(
        home=home,
        db_path=home / "coldforge.db",
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        model=os.environ.get("COLDFORGE_MODEL", "claude-haiku-4-5-20251001"),
        tavily_api_key=os.environ.get("TAVILY_API_KEY") or None,
        from_name=os.environ.get("COLDFORGE_FROM_NAME", "").strip(),
        from_email=os.environ.get("COLDFORGE_FROM_EMAIL", "").strip(),
        smtp_host=os.environ.get("SMTP_HOST") or None,
        smtp_port=int(os.environ.get("SMTP_PORT", "587") or 587),
        smtp_user=os.environ.get("SMTP_USER") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        smtp_starttls=(os.environ.get("SMTP_STARTTLS", "true").lower() in _TRUE),
        imap_host=os.environ.get("IMAP_HOST") or None,
        imap_port=int(os.environ.get("IMAP_PORT", "993") or 993),
        imap_user=os.environ.get("IMAP_USER") or None,
        imap_password=os.environ.get("IMAP_PASSWORD") or None,
        daily_limit=int(os.environ.get("COLDFORGE_DAILY_LIMIT", "40") or 40),
        send_window=_parse_window(os.environ.get("COLDFORGE_SEND_WINDOW", "09:00-17:00")),
        send_days=_parse_days(os.environ.get("COLDFORGE_SEND_DAYS", "mon-fri")),
        min_gap_seconds=int(os.environ.get("COLDFORGE_MIN_GAP_SECONDS", "45") or 45),
    )
