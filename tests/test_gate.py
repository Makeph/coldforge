from datetime import datetime, time

from coldforge.config import Settings
from coldforge.db import Store
from coldforge.gate import check_message
from coldforge.models import Campaign, Lead, Message


def _settings(tmp_path):
    return Settings(
        home=tmp_path, db_path=tmp_path / "db.sqlite", anthropic_api_key=None,
        model="x", tavily_api_key=None, from_name="A", from_email="a@x.fr",
        smtp_host=None, smtp_port=587, smtp_user=None, smtp_password=None,
        smtp_starttls=True, imap_host=None, imap_port=993, imap_user=None,
        imap_password=None, daily_limit=40, send_window=(time(0), time(23, 59)),
        send_days=set(range(7)), min_gap_seconds=0, postal_address="1 rue Exemple, Lyon",
    )


def _message(store):
    lead = store.upsert_lead(Lead("person@x.fr", verify_status="ok"))
    store.set_verify_status(lead.id, "ok")
    campaign = store.create_campaign(Campaign("c"))
    return Message(campaign.id, lead.id, 0, datetime(2030, 1, 1, 10), subject="Bonjour",
                   body="Bonjour. Pour vous désinscrire, répondez non.\n1 rue Exemple, Lyon")


def test_gate_passing_and_each_refusal(tmp_path):
    settings = _settings(tmp_path)
    with Store(settings.db_path) as store:
        message = _message(store)
        now = datetime(2030, 1, 1, 10)
        assert check_message(store, settings, message, now).ok
        store.suppress("person@x.fr")
        assert "suppressed" in " ".join(check_message(store, settings, message, now).reasons)
        store.unsuppress("person@x.fr")
        store.set_verify_status(message.lead_id, "risky")
        assert "verify_status" in " ".join(check_message(store, settings, message, now).reasons)
        store.set_verify_status(message.lead_id, "ok")
        message.body = "No footer"
        reasons = check_message(store, settings, message, now).reasons
        assert "missing unsubscribe line" in reasons and "missing postal address" in reasons
        message.body = "Unsubscribe\n1 rue Exemple, Lyon"
        assert "quiet-hours" in " ".join(check_message(store, settings, message, datetime(2030, 1, 1, 22)).reasons)
        store.record_reply(message.lead_id, message.campaign_id)
        assert "already replied" in " ".join(check_message(store, settings, message, now).reasons)
