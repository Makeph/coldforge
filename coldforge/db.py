"""SQLite storage layer — a single file, no server, safe to delete and rebuild.

Mirrors the storage philosophy of cold-cli (local SQLite by default) but kept
deliberately small. All timestamps are stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Campaign, Lead, Message, Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    first_name  TEXT DEFAULT '',
    last_name   TEXT DEFAULT '',
    company     TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    website     TEXT DEFAULT '',
    linkedin    TEXT DEFAULT '',
    custom      TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    source      TEXT DEFAULT '',
    url         TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'draft',
    from_email  TEXT DEFAULT '',
    sequence    TEXT DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id       INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    step          INTEGER NOT NULL,
    scheduled_at  TEXT NOT NULL,
    template_id   TEXT DEFAULT '',
    subject       TEXT DEFAULT '',
    body          TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'scheduled',
    sent_at       TEXT,
    variant       TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    UNIQUE(campaign_id, lead_id, step)
);

CREATE TABLE IF NOT EXISTS replies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id  INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    detected_at  TEXT NOT NULL,
    source       TEXT DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_messages_due
    ON messages(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_signals_lead ON signals(lead_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    """Thin synchronous wrapper around a SQLite connection."""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── leads ──────────────────────────────────────────────────────────────
    def upsert_lead(self, lead: Lead) -> Lead:
        lead.email = lead.email.strip().lower()
        cur = self.conn.execute(
            """INSERT INTO leads(email,first_name,last_name,company,title,website,
                                 linkedin,custom,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(email) DO UPDATE SET
                 first_name=excluded.first_name, last_name=excluded.last_name,
                 company=excluded.company, title=excluded.title,
                 website=excluded.website, linkedin=excluded.linkedin,
                 custom=excluded.custom
               RETURNING id""",
            (
                lead.email, lead.first_name, lead.last_name,
                lead.company, lead.title, lead.website, lead.linkedin,
                json.dumps(lead.custom), _now(),
            ),
        )
        lead.id = cur.fetchone()[0]
        self.conn.commit()
        return lead

    def get_lead(self, lead_id: int) -> Lead | None:
        row = self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return self._row_to_lead(row) if row else None

    def find_lead(self, needle: str) -> Lead | None:
        """Resolve a lead by id, exact email, or unique email prefix."""
        if needle.isdigit():
            return self.get_lead(int(needle))
        row = self.conn.execute(
            "SELECT * FROM leads WHERE email=?", (needle.strip().lower(),)
        ).fetchone()
        if row:
            return self._row_to_lead(row)
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE email LIKE ?", (f"{needle.strip().lower()}%",)
        ).fetchall()
        return self._row_to_lead(rows[0]) if len(rows) == 1 else None

    def list_leads(self) -> list[Lead]:
        rows = self.conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
        return [self._row_to_lead(r) for r in rows]

    @staticmethod
    def _row_to_lead(row: sqlite3.Row) -> Lead:
        return Lead(
            id=row["id"], email=row["email"], first_name=row["first_name"],
            last_name=row["last_name"], company=row["company"], title=row["title"],
            website=row["website"], linkedin=row["linkedin"],
            custom=json.loads(row["custom"] or "{}"),
        )

    # ── signals ────────────────────────────────────────────────────────────
    def add_signal(self, sig: Signal) -> Signal:
        cur = self.conn.execute(
            "INSERT INTO signals(lead_id,text,source,url,created_at) VALUES(?,?,?,?,?) RETURNING id",
            (sig.lead_id, sig.text, sig.source, sig.url, _now()),
        )
        sig.id = cur.fetchone()[0]
        self.conn.commit()
        return sig

    def signals_for(self, lead_id: int) -> list[Signal]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE lead_id=? ORDER BY id DESC", (lead_id,)
        ).fetchall()
        return [
            Signal(id=r["id"], lead_id=r["lead_id"], text=r["text"],
                   source=r["source"], url=r["url"], created_at=_dt(r["created_at"]))
            for r in rows
        ]

    # ── campaigns ──────────────────────────────────────────────────────────
    def create_campaign(self, c: Campaign) -> Campaign:
        cur = self.conn.execute(
            "INSERT INTO campaigns(name,status,from_email,sequence,created_at) "
            "VALUES(?,?,?,?,?) RETURNING id",
            (c.name, c.status, c.from_email, json.dumps(c.sequence), _now()),
        )
        c.id = cur.fetchone()[0]
        self.conn.commit()
        return c

    def get_campaign(self, name_or_id: str) -> Campaign | None:
        if str(name_or_id).isdigit():
            row = self.conn.execute(
                "SELECT * FROM campaigns WHERE id=?", (int(name_or_id),)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM campaigns WHERE name=?", (name_or_id,)
            ).fetchone()
        if not row:
            return None
        return Campaign(
            id=row["id"], name=row["name"], status=row["status"],
            from_email=row["from_email"], sequence=json.loads(row["sequence"] or "[]"),
            created_at=_dt(row["created_at"]),
        )

    def set_campaign_status(self, campaign_id: int, status: str) -> None:
        self.conn.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
        self.conn.commit()

    def list_campaigns(self) -> list[Campaign]:
        rows = self.conn.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
        return [
            Campaign(id=r["id"], name=r["name"], status=r["status"],
                     from_email=r["from_email"], sequence=json.loads(r["sequence"] or "[]"),
                     created_at=_dt(r["created_at"]))
            for r in rows
        ]

    # ── messages ───────────────────────────────────────────────────────────
    def schedule_message(self, m: Message) -> Message | None:
        """Insert a scheduled message; returns None if it already exists."""
        try:
            cur = self.conn.execute(
                """INSERT INTO messages(campaign_id,lead_id,step,scheduled_at,template_id,
                                        subject,body,status,variant)
                   VALUES(?,?,?,?,?,?,?,?,?) RETURNING id""",
                (m.campaign_id, m.lead_id, m.step, m.scheduled_at.isoformat(timespec="seconds"),
                 m.template_id, m.subject, m.body, m.status, m.variant),
            )
            m.id = cur.fetchone()[0]
            self.conn.commit()
            return m
        except sqlite3.IntegrityError:
            return None

    def due_messages(self, now: datetime) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE status='scheduled' AND scheduled_at<=? "
            "ORDER BY scheduled_at",
            (now.isoformat(timespec="seconds"),),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def messages_for_campaign(self, campaign_id: int) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE campaign_id=? ORDER BY scheduled_at", (campaign_id,)
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def mark_message(self, message_id: int, status: str, *, error: str = "") -> None:
        sent = _now() if status == "sent" else None
        self.conn.execute(
            "UPDATE messages SET status=?, sent_at=COALESCE(?,sent_at), error=? WHERE id=?",
            (status, sent, error, message_id),
        )
        self.conn.commit()

    def cancel_pending_for_lead(self, campaign_id: int, lead_id: int) -> int:
        """Cancel still-scheduled steps for a lead (used when they reply)."""
        cur = self.conn.execute(
            "UPDATE messages SET status='canceled' "
            "WHERE campaign_id=? AND lead_id=? AND status='scheduled'",
            (campaign_id, lead_id),
        )
        self.conn.commit()
        return cur.rowcount

    def sent_today(self, day_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE status='sent' AND substr(sent_at,1,10)=?",
            (day_iso,),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"], campaign_id=row["campaign_id"], lead_id=row["lead_id"],
            step=row["step"], scheduled_at=_dt(row["scheduled_at"]),
            template_id=row["template_id"], subject=row["subject"], body=row["body"],
            status=row["status"], sent_at=_dt(row["sent_at"]), variant=row["variant"],
            error=row["error"],
        )

    # ── replies ────────────────────────────────────────────────────────────
    def record_reply(self, lead_id: int, campaign_id: int | None, source: str = "manual") -> None:
        self.conn.execute(
            "INSERT INTO replies(lead_id,campaign_id,detected_at,source) VALUES(?,?,?,?)",
            (lead_id, campaign_id, _now(), source),
        )
        self.conn.commit()

    def has_replied(self, lead_id: int, campaign_id: int | None = None) -> bool:
        if campaign_id is None:
            row = self.conn.execute(
                "SELECT 1 FROM replies WHERE lead_id=? LIMIT 1", (lead_id,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM replies WHERE lead_id=? AND "
                "(campaign_id=? OR campaign_id IS NULL) LIMIT 1",
                (lead_id, campaign_id),
            ).fetchone()
        return row is not None
