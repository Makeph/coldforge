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
        # A one-click opt-out path keeps recipients (and filters) on your side —
        # replies with unsubscribe intent are auto-suppressed by `reply scan`.
        msg["List-Unsubscribe"] = f"<mailto:{self.s.from_email}?subject=unsubscribe>"
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
    """Poll IMAP, record replies from known leads, and triage each one.

    Matching is by sender address against the leads table. Every new reply is
    classified (interested / not_interested / unsubscribe / ooo / other — see
    :mod:`coldforge.replies`); unsubscribe intent lands the address on the
    suppression list immediately. Returns the number of *new* replies recorded.
    Best-effort: returns 0 if IMAP isn't configured.
    """
    from .replies import classify_reply

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
            typ, msg_data = imap.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1].decode(errors="ignore")
            _, addr = parseaddr(_header_value(raw, "from"))
            lead = known.get(addr.lower())
            if not lead or lead.id is None or store.has_replied(lead.id):
                continue
            subject = _header_value(raw, "subject")
            body = _fetch_body_snippet(imap, mid)
            category = classify_reply(subject, body, settings)
            store.record_reply(lead.id, None, source="imap", category=category)
            if category == "unsubscribe":
                store.suppress(lead.email, reason="reply asked to stop")
            recorded += 1
    return recorded


def _header_value(raw_headers: str, name: str) -> str:
    for line in raw_headers.splitlines():
        if line.lower().startswith(f"{name}:"):
            return line.partition(":")[2].strip()
    return ""


def _fetch_body_snippet(imap: imaplib.IMAP4_SSL, mid: bytes, *, size: int = 1500) -> str:
    """First bytes of the message text, best-effort — enough for triage,
    cheap enough to do per reply. Empty string on any server quirk."""
    try:
        typ, msg_data = imap.fetch(mid, f"(BODY.PEEK[TEXT]<0.{size}>)")
        if typ == "OK" and msg_data and msg_data[0] and isinstance(msg_data[0], tuple):
            return msg_data[0][1].decode(errors="ignore")
    except (imaplib.IMAP4.error, OSError):
        pass
    return ""
