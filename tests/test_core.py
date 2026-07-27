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
from coldforge.templates import get as get_template
from coldforge.templates import load_all, missing_vars, render


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
    _seed_campaign(store, settings)
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


# ── verification (offline: check_mx=False) ───────────────────────────────────
def test_verify_email_offline():
    from coldforge.verify import verify_email

    assert verify_email("dana@acme.io", check_mx=False).status == "ok"
    assert verify_email("not-an-email", check_mx=False).status == "invalid"
    assert verify_email("x@mailinator.com", check_mx=False).status == "invalid"
    risky = verify_email("info@acme.io", check_mx=False)
    assert risky.status == "risky" and "role" in risky.reasons[0]


def test_invalid_lead_never_scheduled(store, settings):
    lead = store.upsert_lead(Lead(email="dead@x.io", custom={"pain": "p", "outcome": "o"}))
    store.set_verify_status(lead.id, "invalid")
    camp = store.create_campaign(Campaign(name="cv", from_email="d@me.com",
                                          sequence=normalize_sequence(
                                              [{"template": "sales_pain_point"}])))
    n = schedule_campaign(store, camp, store.list_leads(), settings,
                          start=datetime(2030, 1, 1, 9, 0))
    assert n == 0


# ── suppression list ─────────────────────────────────────────────────────────
def test_suppression_roundtrip(store):
    store.suppress("Out@X.io", reason="asked to stop")
    assert store.is_suppressed("out@x.io")
    assert store.list_suppressions()[0][0] == "out@x.io"
    assert store.unsuppress("out@x.io")
    assert not store.is_suppressed("out@x.io")


def test_tick_skips_suppressed_and_cancels(store, settings):
    camp, _ = _seed_campaign(store, settings)
    store.suppress("l1@x.io", reason="opt-out")
    res = tick(store, settings, DryRunSender(), now=datetime(2030, 1, 1, 10, 0))
    sent_emails = {store.get_lead(m.lead_id).email for m in res.sent}
    assert "l1@x.io" not in sent_emails
    assert len(res.skipped_suppressed) == 1
    # the suppressed lead's follow-up (step 1) was canceled too
    statuses = {m.step: m.status for m in store.messages_for_campaign(camp.id)
                if store.get_lead(m.lead_id).email == "l1@x.io"}
    assert statuses == {0: "skipped", 1: "canceled"}


# ── reply triage (keyword path, no key) ──────────────────────────────────────
def test_classify_reply_keywords(settings):
    from coldforge.replies import classify_reply

    assert classify_reply("Re: x", "Merci de me désinscrire de vos emails", settings) == "unsubscribe"
    assert classify_reply("Automatic reply", "I am out of office until Monday", settings) == "ooo"
    assert classify_reply("Re: x", "Pas intéressé, merci", settings) == "not_interested"
    assert classify_reply("Re: x", "Intéressé — on peut échanger cette semaine ?",
                          settings) == "interested"
    assert classify_reply("Re: x", "qui êtes-vous ?", settings) == "other"


def test_reply_category_stored(store):
    lead = store.upsert_lead(Lead(email="r@x.io"))
    store.record_reply(lead.id, None, source="manual", category="interested")
    assert store.reply_categories() == {"interested": 1}


# ── content lint ─────────────────────────────────────────────────────────────
def test_lint_flags_spam_and_unfilled_vars():
    from coldforge.lint import lint_draft

    bad = lint_draft("GRATUIT — cliquez ici !!!",
                     "Act now! Buy now! 100% free guarantee, cliquez ici {{name}} "
                     "http://a.io http://b.io")
    assert not bad.ok
    messages = " ".join(i.message for i in bad.issues)
    assert "spam-trigger" in messages and "{{variables}}" in messages

    good = lint_draft("Acme + reporting ?",
                      "Bonjour Sam, j'ai vu votre dernier article sur le SEO local. "
                      "On aide les agences comme la vôtre à prouver leur travail chaque mois. "
                      "Est-ce un sujet chez vous en ce moment ?")
    assert good.ok and good.score == 100


# ── ICP heuristics (offline) ─────────────────────────────────────────────────
def test_icp_heuristic_build_and_score(settings):
    from coldforge.icp import _heuristic_icp, score_lead

    text = ("Acme est le registre de décisions des agences SEO, Ads et growth. "
            "Chaque matin le brief détecte budgets, tracking et dérives. "
            "Chaque mois la preuve part au client des agences.")
    icp = _heuristic_icp("acme.fr", text)
    assert icp["source"] == "heuristic" and "agences" in icp["keywords"]

    fit = Lead(email="d@agence-seo.fr", company="Agence Lumen", title="Fondateur, agence SEO")
    misfit = Lead(email="j@bakery.fr", company="Boulangerie Paul", title="Gérant")
    fit_score, fit_reason = score_lead(fit, icp, settings=settings)
    misfit_score, _ = score_lead(misfit, icp, settings=settings)
    assert fit_score > misfit_score
    assert "matches:" in fit_reason


def test_icp_heuristic_has_empty_content_gaps():
    from coldforge.icp import _heuristic_icp

    icp = _heuristic_icp("acme.fr", "Some product text about agencies and reporting.")
    assert icp["content_gaps"] == []


def test_icp_save_load_roundtrip(settings):
    from coldforge.icp import load_icp, save_icp

    icp = {"site": "x.io", "product": "p", "keywords": ["k"], "segments": []}
    save_icp(icp, settings)
    assert load_icp(settings) == icp


# ── db migration ─────────────────────────────────────────────────────────────
def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3

    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        "CREATE TABLE leads (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,"
        " first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', company TEXT DEFAULT '',"
        " title TEXT DEFAULT '', website TEXT DEFAULT '', linkedin TEXT DEFAULT '',"
        " custom TEXT DEFAULT '{}', created_at TEXT NOT NULL);"
        "CREATE TABLE replies (id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL,"
        " campaign_id INTEGER, detected_at TEXT NOT NULL, source TEXT DEFAULT 'manual');"
        "INSERT INTO leads(email, created_at) VALUES('old@x.io', '2025-01-01T00:00:00');"
    )
    conn.commit()
    conn.close()

    s = Store(old)
    lead = s.find_lead("old@x.io")
    assert lead is not None and lead.fit_score is None and lead.verify_status == ""
    s.set_fit(lead.id, 80, "matches")
    s.set_verify_status(lead.id, "ok")
    again = s.find_lead("old@x.io")
    assert again.fit_score == 80 and again.verify_status == "ok"
    s.close()


# ── geo (AI-answer-engine visibility, offline) ───────────────────────────────
def test_geo_check_visibility_no_engines_configured(settings):
    from coldforge.geo import check_visibility

    assert check_visibility("best crm for real estate agents", "acme", settings) == []


def test_geo_engines_empty_without_keys(settings):
    assert settings.geo_engines == []


def test_geo_mentions_and_snippet():
    from coldforge.geo import _mentions, _snippet

    text = "Many tools exist. Acme is the leader for agencies. Others lag behind."
    assert _mentions(text, "Acme")
    assert not _mentions(text, "Northwind")
    assert "Acme is the leader" in _snippet(text, "Acme")
    assert _snippet(text, "Northwind") == "Many tools exist."


# ── content planning (offline, heuristic path) ───────────────────────────────
def test_content_plan_heuristic_uses_gaps_and_keywords(settings):
    from coldforge.content import plan_content

    icp = {
        "site": "acme.fr", "keywords": ["reporting", "agences", "seo", "ads", "budget", "tracking"],
        "content_gaps": [{"topic": "comment prouver son travail SEO", "why": "cherché par les agences"}],
    }
    briefs = plan_content(icp, count=2, settings=settings)
    assert len(briefs) == 2
    assert briefs[0].topic == "comment prouver son travail SEO"
    assert briefs[0].status == "planned"
    assert all(b.id for b in briefs)


def test_content_plan_heuristic_without_gaps_chunks_keywords(settings):
    from coldforge.content import plan_content

    icp = {"site": "acme.fr", "keywords": ["a", "b", "c", "d", "e", "f"], "content_gaps": []}
    briefs = plan_content(icp, count=3, settings=settings)
    assert len(briefs) == 2  # only 2 chunks of 3 fit in 6 keywords
    assert briefs[0].keywords == ["a", "b", "c"]


def test_content_plan_save_load_roundtrip(settings):
    from coldforge.content import ContentBrief, load_plan, save_plan

    briefs = [ContentBrief(id="01-x", topic="X", keywords=["x"], angle="a")]
    save_plan(briefs, settings)
    loaded = load_plan(settings)
    assert loaded == briefs


def test_content_draft_heuristic_fallback(settings):
    from coldforge.content import ContentBrief, draft_article

    brief = ContentBrief(id="01-x", topic="X topic", keywords=["x", "y"], angle="an angle")
    draft_article(brief, {"site": "acme.fr"}, settings, force_heuristic=True)
    assert brief.status == "drafted"
    assert "X topic" in brief.body and "an angle" in brief.body


# ── French template pack ─────────────────────────────────────────────────────
def test_fr_agency_pack_loads(settings):
    pack = load_all()
    fr = [t for t in pack.values() if t.category == "sales-fr"]
    assert {t.id for t in fr} >= {"fr_agence_preuve", "fr_agence_relance",
                                  "fr_agence_derniere_porte"}
    lead = Lead(email="d@agence.fr", first_name="Léa", company="Agence Lumen", id=1,
                custom={"pain": "le client ne voit pas le travail",
                        "outcome": "la preuve mensuelle part toute seule",
                        "observation": "Vu votre étude de cas Google Ads"})
    d = draft_email("fr_agence_preuve", lead, settings=settings, force_template_fill=True)
    assert "Léa" in d.body and "{{" not in d.body.replace("{{sender_name}}", "")
