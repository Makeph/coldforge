"""Sequence scheduling and the safe-send worker (`tick`).

A *sequence* is a list of steps:

```yaml
- template: sales_pain_point      # which template to render
  wait_days: 0                    # days after the previous step
  condition: always               # always | no_reply (default no_reply for steps > 0)
  variant: A                      # optional A/B label
- template: followup_bump
  wait_days: 3
  condition: no_reply
```

When a campaign is activated, every step is pre-scheduled for every lead using
cumulative ``wait_days`` offsets. The worker (``tick``) then enforces the
guardrails at send time and applies the **reply → cancel** rule: a ``no_reply``
step is skipped (and the rest of that lead's sequence canceled) once a reply is
recorded. This mirrors coldflow's silent-reply / no-reply follow-up behaviour
without needing a long-running daemon — just run ``tick`` from cron.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from .config import Settings
from .db import Store
from .models import Campaign, Draft, Lead, Message
from .personalize import draft_email

# A sensible default when a campaign is created without a sequence file.
DEFAULT_SEQUENCE: list[dict] = [
    {"template": "sales_pain_point", "wait_days": 0, "condition": "always"},
    {"template": "followup_bump", "wait_days": 3, "condition": "no_reply"},
]


def load_sequence(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "steps" in data:
        data = data["steps"]
    if not isinstance(data, list) or not data:
        raise ValueError("Sequence file must be a non-empty list of steps.")
    return normalize_sequence(data)


def normalize_sequence(steps: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, raw in enumerate(steps):
        if "template" not in raw:
            raise ValueError(f"Step {i} is missing a 'template'.")
        out.append({
            "template": str(raw["template"]),
            "wait_days": int(raw.get("wait_days", 0 if i == 0 else 3)),
            "condition": str(raw.get("condition", "always" if i == 0 else "no_reply")),
            "variant": str(raw.get("variant", "")),
        })
    return out


# ── send-window helpers ──────────────────────────────────────────────────────
def within_window(now: datetime, settings: Settings) -> bool:
    if now.weekday() not in settings.send_days:
        return False
    start, end = settings.send_window
    return start <= now.time() <= end


def next_window_start(now: datetime, settings: Settings) -> datetime:
    """The next datetime that falls inside the configured send window/days."""
    start, _ = settings.send_window
    candidate = now
    for _ in range(14):  # look ahead up to two weeks
        day_start = datetime.combine(candidate.date(), start)
        if candidate.weekday() in settings.send_days and candidate <= day_start:
            return day_start
        if candidate.weekday() in settings.send_days and within_window(candidate, settings):
            return candidate
        candidate = datetime.combine(candidate.date() + timedelta(days=1), time(0, 0))
    return now


# ── scheduling ───────────────────────────────────────────────────────────────
def schedule_campaign(
    store: Store,
    campaign: Campaign,
    leads: list[Lead],
    settings: Settings,
    *,
    start: datetime | None = None,
    personalize: bool = False,
) -> int:
    """Pre-schedule every step for every lead. Returns count of messages created.

    Bodies are rendered now (deterministic template fill by default, or AI if
    ``personalize`` and a key is set) so ``campaign preview`` shows real content.
    A researched signal stored for the lead is woven into the opener.
    """
    sequence = campaign.sequence or DEFAULT_SEQUENCE
    base = start or datetime.now()
    created = 0

    for lead in leads:
        if lead.id is None:
            continue
        signal = next(iter(store.signals_for(lead.id)), None)
        cumulative = 0
        for step_idx, step in enumerate(sequence):
            cumulative += step["wait_days"]
            when = next_window_start(base + timedelta(days=cumulative), settings)
            drafted: Draft = draft_email(
                step["template"], lead, signal=signal, settings=settings,
                force_template_fill=not personalize,
            )
            msg = Message(
                campaign_id=campaign.id,  # type: ignore[arg-type]
                lead_id=lead.id, step=step_idx, scheduled_at=when,
                template_id=step["template"], subject=drafted.subject,
                body=drafted.body, status="scheduled", variant=step["variant"],
            )
            if store.schedule_message(msg):
                created += 1
    return created


# ── the worker ───────────────────────────────────────────────────────────────
class TickResult:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.skipped_replied: list[Message] = []
        self.canceled: int = 0
        self.held_limit: int = 0
        self.held_window: bool = False

    def as_dict(self) -> dict:
        return {
            "sent": len(self.sent),
            "skipped_replied": len(self.skipped_replied),
            "canceled": self.canceled,
            "held_for_daily_limit": self.held_limit,
            "outside_send_window": self.held_window,
        }


def tick(store: Store, settings: Settings, sender, *, now: datetime | None = None,
         dry_run: bool = False) -> TickResult:
    """Process all due messages, honouring guardrails and the reply→cancel rule.

    *sender* is any object with ``send(to, subject, body) -> None`` (see
    :class:`coldforge.sender.SmtpSender` and :class:`coldforge.sender.DryRunSender`).
    """
    now = now or datetime.now()
    result = TickResult()

    if not dry_run and not within_window(now, settings):
        result.held_window = True
        return result

    remaining = settings.daily_limit - store.sent_today(now.date().isoformat())
    if remaining <= 0 and not dry_run:
        result.held_limit = 1
        return result

    for msg in store.due_messages(now):
        # reply → cancel: drop conditional follow-ups once the lead replied.
        step_condition = _condition_for(store, msg)
        if step_condition == "no_reply" and store.has_replied(msg.lead_id, msg.campaign_id):
            store.mark_message(msg.id, "skipped")  # type: ignore[arg-type]
            result.skipped_replied.append(msg)
            result.canceled += store.cancel_pending_for_lead(msg.campaign_id, msg.lead_id)
            continue

        if not dry_run and remaining <= 0:
            result.held_limit += 1
            break

        lead = store.get_lead(msg.lead_id)
        if lead is None:
            store.mark_message(msg.id, "failed", error="lead missing")  # type: ignore[arg-type]
            continue

        if dry_run:
            result.sent.append(msg)
            continue

        try:
            sender.send(lead.email, msg.subject, msg.body)
            store.mark_message(msg.id, "sent")  # type: ignore[arg-type]
            result.sent.append(msg)
            remaining -= 1
            # jittered pacing between real sends
            _pace(settings)
        except Exception as exc:  # noqa: BLE001
            store.mark_message(msg.id, "failed", error=str(exc)[:300])  # type: ignore[arg-type]

    return result


def _condition_for(store: Store, msg: Message) -> str:
    campaign = store.get_campaign(str(msg.campaign_id))
    seq = (campaign.sequence if campaign else None) or DEFAULT_SEQUENCE
    if 0 <= msg.step < len(seq):
        return seq[msg.step].get("condition", "no_reply" if msg.step else "always")
    return "no_reply"


def _pace(settings: Settings) -> None:
    import time as _time

    gap = settings.min_gap_seconds
    if gap > 0:
        _time.sleep(gap + random.uniform(0, gap * 0.5))
