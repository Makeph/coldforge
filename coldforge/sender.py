"""Email transport: SMTP sending + optional IMAP reply detection.

* :class:`DryRunSender` — prints what *would* be sent; the safe default.
* :class:`SmtpSender`    — real send over SMTP (STARTTLS or implicit TLS).
* :func:`scan_replies`   — poll IMAP for replies and record them so the
  sequence worker can auto-cancel follow-ups.
"""

from __future__ import annotations

import imaplib
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from .config import Settings
from .db import Store


class DryRunSender:
    """No network. Records sends so callers/tests can assert on them."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


class SmtpSender:
    def __init__(self, settings: Settings):
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured — cannot send.")
        if not settings.from_email:
            raise RuntimeError("COLDFORGE_FROM_EMAIL is not configured — cannot send.")
        self.s = settings

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = formataddr((self.s.from_name or "", self.s.from_email))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        if self.s.smtp_starttls:
            with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30) as server:
                server.starttls()
                if self.s.smtp_user:
                    server.login(self.s.smtp_user, self.s.smtp_password or "")
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=30) as server:
                if self.s.smtp_user:
                    server.login(self.s.smtp_user, self.s.smtp_password or "")
                server.send_message(msg)


def make_sender(settings: Settings, *, dry_run: bool):
    if dry_run or not settings.can_send:
        return DryRunSender()
    return SmtpSender(settings)


def scan_replies(store: Store, settings: Settings, *, mailbox: str = "INBOX",
                 limit: int = 200) -> int:
    """Poll IMAP and record a reply for any known lead who emailed us.

    Matching is by sender address against the leads table. Returns the number of
    *new* replies recorded. Best-effort: returns 0 if IMAP isn't configured.
    """
    if not settings.can_detect_replies:
        return 0

    known = {lead.email.lower(): lead for lead in store.list_leads()}
    if not known:
        return 0

    recorded = 0
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:  # type: ignore[arg-type]
        imap.login(settings.imap_user, settings.imap_password)  # type: ignore[arg-type]
        imap.select(mailbox)
        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            return 0
        ids = data[0].split()[-limit:]
        for mid in reversed(ids):
            typ, msg_data = imap.fetch(mid, "(BODY[HEADER.FIELDS (FROM)])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1].decode(errors="ignore")
            _, addr = parseaddr(raw)
            lead = known.get(addr.lower())
            if lead and lead.id is not None and not store.has_replied(lead.id):
                store.record_reply(lead.id, None, source="imap")
                recorded += 1
    return recorded
