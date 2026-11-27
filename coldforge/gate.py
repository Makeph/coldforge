"""Deterministic approval gate applied immediately before a message is approved."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .db import Store
from .lint import compliance_issues, lint_draft
from .models import Message


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_message(
    store: Store, settings: Settings, message: Message, now: datetime
) -> GateResult:
    """Evaluate every guard independently so a reviewer sees all defects at once."""
    reasons: list[str] = []
    lead = store.get_lead(message.lead_id)
    if lead is None:
        return GateResult(False, ["recipient missing"])
    if store.is_suppressed(lead.email):
        reasons.append("recipient address is suppressed")
    if lead.verify_status != "ok":
        reasons.append(f"verify_status is {lead.verify_status or 'unset'}, expected ok")
    if store.sent_today(now.date().isoformat()) >= settings.daily_limit:
        reasons.append("daily sending limit reached")
    local_now = _recipient_time(now, lead.custom.get("timezone", ""))
    start, end = getattr(settings, "gate_window", settings.send_window)
    if not start <= local_now.time().replace(tzinfo=None) <= end:
        reasons.append("outside recipient quiet-hours window")
    reasons.extend(compliance_issues(message.body, getattr(settings, "postal_address", "")))
    report = lint_draft(message.subject, message.body)
    if any(issue.severity == "block" for issue in report.issues):
        reasons.append("message lint has a blocking issue")
    if store.has_replied(message.lead_id, message.campaign_id):
        reasons.append("recipient already replied")
    return GateResult(not reasons, reasons)


def _recipient_time(now: datetime, timezone_name: str) -> datetime:
    if not timezone_name or now.tzinfo is None:
        return now
    try:
        return now.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        return now
