"""The funnel: N contacted → M replied → K calls → won.

``stats`` answers "what went out and who answered". That stops one step short of
the only question a campaign actually has to answer — *is this worth doing
again?* — which needs the steps after the reply: the call, the quote, the deal.

The first steps are **derived** from data the engine already owns (scheduled and
sent messages, the replies table and its triage category), so they cost no
bookkeeping. The later ones are recorded by hand with ``coldforge stage set`` —
there is no honest way to detect a booked call from an inbox.

Each step's percentage is measured against the step above it, so a bad row is
the one to fix: 40 contacted → 2 replies is a copy problem, 7 replies → 0 calls
is an offer problem.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import Store
from .models import Campaign


@dataclass
class Step:
    """One rung of the funnel."""

    key: str
    label: str
    count: int
    of_previous: float | None = None   # % of the step above (None for the first)
    derived: bool = True               # False = recorded by hand
    hint: str = ""                     # shown next to the row


@dataclass
class Funnel:
    campaign: str
    steps: list[Step]

    @property
    def top(self) -> int:
        return self.steps[0].count if self.steps else 0

    def as_dict(self) -> dict:
        return {
            "campaign": self.campaign,
            "steps": [
                {"key": s.key, "label": s.label, "count": s.count,
                 "of_previous": s.of_previous, "derived": s.derived}
                for s in self.steps
            ],
        }


# label + whether the step is derived, in pipeline order
_LAYOUT: tuple[tuple[str, str, bool], ...] = (
    ("enrolled", "enrolled", True),
    ("contacted", "contacted", True),
    ("replied", "replied", True),
    ("interested", "of which interested", True),
    ("call_booked", "call booked", False),
    ("call_done", "call done", False),
    ("quoted", "quoted", False),
    ("won", "won", False),
)


def compute_funnel(store: Store, campaign: Campaign) -> Funnel:
    """Build the funnel for *campaign* from messages, replies and stages."""
    cid = campaign.id
    messages = store.messages_for_campaign(cid) if cid is not None else []

    enrolled = {m.lead_id for m in messages}
    contacted = {m.lead_id for m in messages if m.status == "sent"}
    replied = {lid for lid in enrolled if store.has_replied(lid, cid)}
    interested = store.leads_with_reply_category("interested", cid) & enrolled

    counts = {
        "enrolled": len(enrolled),
        "contacted": len(contacted),
        "replied": len(replied),
        "interested": len(interested),
    }
    for key in ("call_booked", "call_done", "quoted", "won"):
        counts[key] = len(store.leads_at_stage(key, cid) & enrolled)

    steps: list[Step] = []
    previous: int | None = None
    for key, label, derived in _LAYOUT:
        count = counts[key]
        # "interested" is a slice of the replies, not a rung below them — so it
        # is measured against the replies and never becomes the new baseline.
        base = counts["replied"] if key == "interested" else previous
        pct = (count / base * 100) if base else None
        steps.append(Step(key=key, label=label, count=count, of_previous=pct,
                          derived=derived))
        if key != "interested":
            previous = count

    lost = len(store.leads_at_stage("lost", cid) & enrolled)
    if lost:
        steps.append(Step(key="lost", label="lost", count=lost, derived=False))
    return Funnel(campaign=campaign.name, steps=steps)
