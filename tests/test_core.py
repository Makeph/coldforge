"""Offline tests for the coldforge core — no network, no API keys required."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from coldforge.config import Settings, _parse_days, _parse_window
from coldforge.db import Store
from coldforge.models import Campaign, Lead, Signal
from coldforge.personalize import draft_email
from coldforge.sender import DryRunSender
from coldforge.sequence import normalize_sequence, schedule_campaign, tick, within_window
from coldforge.templates import load_all, missing_vars, render
from coldforge.templates import get as get_template


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        home=tmp_path, db_path=tmp_path / "t.db",
        anthropic_api_key=None, model="x", tavily_api_key=None,
        from_name="Dana Doe", from_email="dana@me.com",
        smtp_host=None, smtp_port=587, smtp_user=None, smtp_password=None,
        smtp_starttls=True, imap_host=None, imap_port=993, imap_user=None,
        imap_password=None, daily_limit=40,
        send_window=(time(0, 0), time(23, 59)), send_days={0, 1, 2, 3, 4, 5, 6},
        min_gap_seconds=0,
    )


@pytest.fixture
def store(settings) -> Store:
    s = Store(settings.db_path)
    yield s
    s.close()


# ── config parsing ───────────────────────────────────────────────────────────
def test_parse_window_and_days():
    assert _parse_window("09:30-17:15") == (time(9, 30), time(17, 15))
    assert _parse_window("garbage") == (time(9, 0), time(17, 0))
    assert _parse_days("mon-fri") == {0, 1, 2, 3, 4}
    assert _parse_days("mon,wed,fri") == {0, 2, 4}


# ── templates ────────────────────────────────────────────────────────────────
def test_pack_loads_and_has_categories():
    pack = load_all()
    assert pack, "template pack should not be empty"
    cats = {t.category for t in pack.values()}
    assert {"sales", "recruiting", "follow-up"} <= cats


def test_render_leaves_unknown_vars_visible():
    out = render("Hi {{first_name}}, re {{company}}", {"first_name": "Sam"})
    assert "Sam" in out and "{{company}}" in out


def test_missing_vars_reported():
    t = get_template("sales_pain_point")
    miss = missing_vars(t, {"first_name": "Sam"})
    assert "company" in miss


# ── leads + db ───────────────────────────────────────────────────────────────
def test_lead_upsert_and_find(store):
    lead = store.upsert_lead(Lead(email="A@Acme.IO", first_name="Al", company="Acme",
                                  custom={"pain": "x"}))
    assert lead.id and lead.email == "a@acme.io"  # normalized
    again = store.upsert_lead(Lead(email="a@acme.io", first_name="Alex", company="Acme"))
    assert again.id == lead.id  # upsert, not duplicate
    assert store.find_lead("a@acme.io").first_name == "Alex"
    assert store.find_lead(str(lead.id)).company == "Acme"


def test_signal_roundtrip(store):
    lead = store.upsert_lead(Lead(email="s@x.io"))
    store.add_signal(Signal(lead_id=lead.id, text="raised a seed round", source="duckduckgo"))
    sigs = store.signals_for(lead.id)
    assert sigs and sigs[0].text == "raised a seed round"


# ── personalization (template-fill path, no key) ─────────────────────────────
def test_draft_template_fill_uses_signal(settings):
    lead = Lead(email="x@y.io", first_name="Sam", company="Northwind", id=1,
                custom={"pain": "p", "outcome": "o"})
    sig = Signal(lead_id=1, text="just shipped a v2 launch", source="x")
    d = draft_email("sales_pain_point", lead, signal=sig, settings=settings,
                    force_template_fill=True)
    assert not d.personalized
    assert "Sam" in d.body
    assert "just shipped a v2 launch" in d.body  # signal injected into opener
    assert d.subject  # rendered


# ── sequence scheduling + safe send + reply-cancel ───────────────────────────
def _seed_campaign(store, settings):
    for i in range(3):
        store.upsert_lead(Lead(email=f"l{i}@x.io", first_name=f"L{i}", company="Co",
                               custom={"pain": "p", "outcome": "o"}))
    seq = normalize_sequence([
        {"template": "sales_pain_point", "wait_days": 0, "condition": "always"},
        {"template": "followup_bump", "wait_days": 3, "condition": "no_reply"},
    ])
    camp = store.create_campaign(Campaign(name="c1", from_email="dana@me.com", sequence=seq))
    n = schedule_campaign(store, camp, store.list_leads(), settings,
                          start=datetime(2030, 1, 1, 9, 0))
    return camp, n


def test_schedule_creates_all_steps(store, settings):
    camp, n = _seed_campaign(store, settings)
    assert n == 6  # 3 leads × 2 steps
    msgs = store.messages_for_campaign(camp.id)
    assert {m.step for m in msgs} == {0, 1}


def test_tick_sends_due_step_zero_only(store, settings):
    camp, _ = _seed_campaign(store, settings)
    sender = DryRunSender()
    res = tick(store, settings, sender, now=datetime(2030, 1, 1, 10, 0))
    # only step 0 is due on day 1; step 1 is +3 days
    assert len(res.sent) == 3
    assert all(m.step == 0 for m in res.sent)


def test_reply_cancels_followups(store, settings):
    camp, _ = _seed_campaign(store, settings)
    sender = DryRunSender()
    tick(store, settings, sender, now=datetime(2030, 1, 1, 10, 0))  # send step 0
    lead = store.find_lead("l0@x.io")
    store.record_reply(lead.id, camp.id)
    store.cancel_pending_for_lead(camp.id, lead.id)
    # 4 days later the surviving step-1 messages fire for the 2 non-repliers
    res = tick(store, settings, sender, now=datetime(2030, 1, 5, 10, 0))
    sent_leads = {m.lead_id for m in res.sent}
    assert lead.id not in sent_leads
    assert len(res.sent) == 2


def test_daily_limit_holds_sends(store, settings):
    settings = Settings(**{**settings.__dict__, "daily_limit": 2})
    _seed_campaign(store, settings)
    res = tick(store, settings, DryRunSender(), now=datetime(2030, 1, 1, 10, 0))
    assert len(res.sent) == 2
    assert res.held_limit >= 1


def test_within_window_respects_days():
    s = Settings(**{**_min_settings().__dict__,
                    "send_window": (time(9, 0), time(17, 0)), "send_days": {0, 1, 2, 3, 4}})
    assert within_window(datetime(2030, 1, 4, 10, 0), s)        # Friday 10:00
    assert not within_window(datetime(2030, 1, 5, 10, 0), s)    # Saturday
    assert not within_window(datetime(2030, 1, 4, 20, 0), s)    # Friday 20:00


def _min_settings() -> Settings:
    from pathlib import Path

    return Settings(
        home=Path("."), db_path=Path("x.db"), anthropic_api_key=None, model="x",
        tavily_api_key=None, from_name="", from_email="", smtp_host=None, smtp_port=587,
        smtp_user=None, smtp_password=None, smtp_starttls=True, imap_host=None,
        imap_port=993, imap_user=None, imap_password=None, daily_limit=40,
        send_window=(time(9, 0), time(17, 0)), send_days={0, 1, 2, 3, 4}, min_gap_seconds=0,
    )
