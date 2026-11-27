"""SQLite storage layer — a single file, no server, safe to delete and rebuild.

Mirrors the storage philosophy of cold-cli (local SQLite by default) but kept
deliberately small. All timestamps are stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .companies import Company
from .models import Campaign, Lead, Message, Signal, Stage

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
    fit_score   INTEGER,
    fit_reason  TEXT DEFAULT '',
    verify_status TEXT DEFAULT '',
    company_id  INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    domain      TEXT,
    website     TEXT DEFAULT '',
    city        TEXT DEFAULT '',
    category    TEXT DEFAULT '',
    phone       TEXT DEFAULT '',
    source      TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_domain
    ON companies(domain) WHERE domain IS NOT NULL AND domain != '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_name_city_no_domain
    ON companies(lower(name), lower(city)) WHERE domain IS NULL OR domain = '';

CREATE TABLE IF NOT EXISTS company_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    value        TEXT DEFAULT '',
    evidence_url TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    UNIQUE(company_id, key)
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
    approved      INTEGER DEFAULT 0,
    UNIQUE(campaign_id, lead_id, step)
);

CREATE TABLE IF NOT EXISTS replies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id  INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    detected_at  TEXT NOT NULL,
    source       TEXT DEFAULT 'manual',
    category     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id  INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    stage        TEXT NOT NULL,
    note         TEXT DEFAULT '',
    set_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppressions (
    email       TEXT PRIMARY KEY,
    reason      TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_due
    ON messages(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_signals_lead ON signals(lead_id);
CREATE INDEX IF NOT EXISTS idx_stages_lead ON stages(lead_id);
CREATE INDEX IF NOT EXISTS idx_company_signals_company ON company_signals(company_id);
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
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first release to pre-existing DBs.
        SQLite's CREATE IF NOT EXISTS skips existing tables, so new columns in
        SCHEMA never reach an old file without this."""
        lead_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(leads)")}
        for name, ddl in (
            ("fit_score", "fit_score INTEGER"),
            ("fit_reason", "fit_reason TEXT DEFAULT ''"),
            ("verify_status", "verify_status TEXT DEFAULT ''"),
            ("company_id", "company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL"),
        ):
            if name not in lead_cols:
                self.conn.execute(f"ALTER TABLE leads ADD COLUMN {ddl}")
        reply_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(replies)")}
        if "category" not in reply_cols:
            self.conn.execute("ALTER TABLE replies ADD COLUMN category TEXT DEFAULT ''")
        message_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(messages)")}
        if "approved" not in message_cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN approved INTEGER DEFAULT 0")

    # ── companies ─────────────────────────────────────────────────────────
    def upsert_company(self, company: Company) -> Company:
        company.domain = company.domain.strip().lower()
        if company.domain:
            row = self.conn.execute(
                "SELECT id FROM companies WHERE domain=?", (company.domain,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT id FROM companies WHERE lower(name)=lower(?) AND lower(city)=lower(?) "
                "AND (domain IS NULL OR domain='')",
                (company.name, company.city),
            ).fetchone()
        values = (
            company.name, company.domain or None, company.website, company.city,
            company.category, company.phone, company.source,
        )
        if row:
            company.id = int(row[0])
            self.conn.execute(
                "UPDATE companies SET name=?,domain=?,website=?,city=?,category=?,phone=?,source=? "
                "WHERE id=?", (*values, company.id),
            )
        else:
            cur = self.conn.execute(
                "INSERT INTO companies(name,domain,website,city,category,phone,source,created_at) "
                "VALUES(?,?,?,?,?,?,?,?) RETURNING id", (*values, _now()),
            )
            company.id = int(cur.fetchone()[0])
        self.conn.commit()
        return company

    def list_companies(self) -> list[Company]:
        rows = self.conn.execute("SELECT * FROM companies ORDER BY id").fetchall()
        return [self._row_to_company(row) for row in rows]

    def get_company(self, company_id: int) -> Company | None:
        row = self.conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        return self._row_to_company(row) if row else None

    @staticmethod
    def _row_to_company(row: sqlite3.Row) -> Company:
        return Company(
            id=row["id"], name=row["name"], domain=row["domain"] or "",
            website=row["website"] or "", city=row["city"] or "",
            category=row["category"] or "", phone=row["phone"] or "",
            source=row["source"] or "", created_at=_dt(row["created_at"]),
        )

    def add_company_signal(
        self, company_id: int, key: str, value: str, evidence_url: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO company_signals(company_id,key,value,evidence_url,created_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(company_id,key) DO UPDATE SET "
            "value=excluded.value,evidence_url=excluded.evidence_url,created_at=excluded.created_at",
            (company_id, key, str(value), evidence_url, _now()),
        )
        self.conn.commit()

    def signals_of(self, company_id: int) -> dict[str, tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT key,value,evidence_url FROM company_signals WHERE company_id=? ORDER BY key",
            (company_id,),
        ).fetchall()
        return {r["key"]: (r["value"], r["evidence_url"]) for r in rows}

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── leads ──────────────────────────────────────────────────────────────
    def upsert_lead(self, lead: Lead) -> Lead:
        lead.email = lead.email.strip().lower()
        cur = self.conn.execute(
            """INSERT INTO leads(email,first_name,last_name,company,title,website,
                                 linkedin,custom,company_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(email) DO UPDATE SET
                 first_name=excluded.first_name, last_name=excluded.last_name,
                 company=excluded.company, title=excluded.title,
                 website=excluded.website, linkedin=excluded.linkedin,
                 custom=excluded.custom, company_id=COALESCE(excluded.company_id,leads.company_id)
               RETURNING id""",
            (
                lead.email, lead.first_name, lead.last_name,
                lead.company, lead.title, lead.website, lead.linkedin,
                json.dumps(lead.custom), lead.company_id, _now(),
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

    def set_fit(self, lead_id: int, score: int, reason: str = "") -> None:
        self.conn.execute(
            "UPDATE leads SET fit_score=?, fit_reason=? WHERE id=?",
            (score, reason, lead_id),
        )
        self.conn.commit()

    def set_verify_status(self, lead_id: int, status: str) -> None:
        self.conn.execute("UPDATE leads SET verify_status=? WHERE id=?", (status, lead_id))
        self.conn.commit()

    @staticmethod
    def _row_to_lead(row: sqlite3.Row) -> Lead:
        return Lead(
            id=row["id"], email=row["email"], first_name=row["first_name"],
            last_name=row["last_name"], company=row["company"], title=row["title"],
            website=row["website"], linkedin=row["linkedin"],
            custom=json.loads(row["custom"] or "{}"),
            fit_score=row["fit_score"], fit_reason=row["fit_reason"] or "",
            verify_status=row["verify_status"] or "",
            company_id=row["company_id"],
        )

    # ── signals ────────────────────────────────────────────────────────────
    def add_signal(self, sig: Signal) -> Signal:
        cur = self.conn.execute(
            "INSERT INTO signals(lead_id,text,source,url,created_at)"
            " VALUES(?,?,?,?,?) RETURNING id",
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
                                        subject,body,status,variant,approved)
                   VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                (m.campaign_id, m.lead_id, m.step, m.scheduled_at.isoformat(timespec="seconds"),
                 m.template_id, m.subject, m.body, m.status, m.variant, int(m.approved)),
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

    def get_message(self, message_id: int) -> Message | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return self._row_to_message(row) if row else None

    def list_outbox(self) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE status='scheduled' ORDER BY scheduled_at,id"
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def set_message_approval(self, message_id: int, approved: bool) -> None:
        self.conn.execute(
            "UPDATE messages SET approved=? WHERE id=? AND status='scheduled'",
            (int(approved), message_id),
        )
        self.conn.commit()

    def reject_message(self, message_id: int) -> None:
        self.conn.execute(
            "UPDATE messages SET approved=0,status='rejected' WHERE id=? AND status='scheduled'",
            (message_id,),
        )
        self.conn.commit()

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
            approved=bool(row["approved"]),
        )

    # ── replies ────────────────────────────────────────────────────────────
    def record_reply(self, lead_id: int, campaign_id: int | None, source: str = "manual",
                     category: str = "") -> None:
        self.conn.execute(
            "INSERT INTO replies(lead_id,campaign_id,detected_at,source,category) "
            "VALUES(?,?,?,?,?)",
            (lead_id, campaign_id, _now(), source, category),
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

    def leads_with_reply_category(self, category: str,
                                  campaign_id: int | None = None) -> set[int]:
        """Lead ids whose reply was triaged as *category* (see :mod:`coldforge.replies`)."""
        sql = "SELECT DISTINCT lead_id FROM replies WHERE category=?"
        args: tuple = (category,)
        if campaign_id is not None:
            sql += " AND (campaign_id=? OR campaign_id IS NULL)"
            args += (campaign_id,)
        return {r[0] for r in self.conn.execute(sql, args)}

    def reply_categories(self, campaign_id: int | None = None) -> dict[str, int]:
        """Count replies per triage category (see :mod:`coldforge.replies`)."""
        if campaign_id is None:
            rows = self.conn.execute(
                "SELECT COALESCE(NULLIF(category,''),'unclassified') c, COUNT(*) "
                "FROM replies GROUP BY c"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT COALESCE(NULLIF(category,''),'unclassified') c, COUNT(*) "
                "FROM replies WHERE campaign_id=? OR campaign_id IS NULL GROUP BY c",
                (campaign_id,),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── stages ─────────────────────────────────────────────────────────────
    def set_stage(self, lead_id: int, stage: str, campaign_id: int | None = None,
                  note: str = "") -> Stage:
        """Record that *lead_id* reached *stage*. Append-only: the table keeps
        the history, :meth:`stage_for` reads the latest entry."""
        row = Stage(lead_id=lead_id, stage=stage, campaign_id=campaign_id, note=note)
        cur = self.conn.execute(
            "INSERT INTO stages(lead_id,campaign_id,stage,note,set_at) "
            "VALUES(?,?,?,?,?) RETURNING id",
            (lead_id, campaign_id, stage, note, _now()),
        )
        row.id = cur.fetchone()[0]
        self.conn.commit()
        return row

    def stage_for(self, lead_id: int, campaign_id: int | None = None) -> Stage | None:
        """The most recently recorded stage for *lead_id* (None if never set)."""
        sql = "SELECT * FROM stages WHERE lead_id=?"
        args: tuple = (lead_id,)
        if campaign_id is not None:
            sql += " AND (campaign_id=? OR campaign_id IS NULL)"
            args += (campaign_id,)
        row = self.conn.execute(sql + " ORDER BY set_at DESC, id DESC LIMIT 1", args).fetchone()
        return self._row_to_stage(row) if row else None

    def leads_at_stage(self, stage: str, campaign_id: int | None = None) -> set[int]:
        """Lead ids that ever reached *stage* — the funnel counts reaching a
        step, not sitting on it, so a won deal still counts as a booked call."""
        sql = "SELECT DISTINCT lead_id FROM stages WHERE stage=?"
        args: tuple = (stage,)
        if campaign_id is not None:
            sql += " AND (campaign_id=? OR campaign_id IS NULL)"
            args += (campaign_id,)
        return {r[0] for r in self.conn.execute(sql, args)}

    def list_stages(self, stage: str = "") -> list[Stage]:
        if stage:
            rows = self.conn.execute(
                "SELECT * FROM stages WHERE stage=? ORDER BY set_at DESC, id DESC", (stage,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM stages ORDER BY set_at DESC, id DESC"
            ).fetchall()
        return [self._row_to_stage(r) for r in rows]

    @staticmethod
    def _row_to_stage(row: sqlite3.Row) -> Stage:
        return Stage(
            id=row["id"], lead_id=row["lead_id"], campaign_id=row["campaign_id"],
            stage=row["stage"], note=row["note"] or "", set_at=_dt(row["set_at"]),
        )

    # ── suppressions ───────────────────────────────────────────────────────
    def suppress(self, email: str, reason: str = "") -> None:
        """Add *email* to the do-not-contact list (idempotent)."""
        self.conn.execute(
            "INSERT INTO suppressions(email,reason,created_at) VALUES(?,?,?) "
            "ON CONFLICT(email) DO NOTHING",
            (email.strip().lower(), reason, _now()),
        )
        self.conn.commit()

    def unsuppress(self, email: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM suppressions WHERE email=?", (email.strip().lower(),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def is_suppressed(self, email: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM suppressions WHERE email=?", (email.strip().lower(),)
        ).fetchone()
        return row is not None

    def list_suppressions(self) -> list[tuple[str, str, str]]:
        rows = self.conn.execute(
            "SELECT email, reason, created_at FROM suppressions ORDER BY created_at"
        ).fetchall()
        return [(r["email"], r["reason"], r["created_at"]) for r in rows]
