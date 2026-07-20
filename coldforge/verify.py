"""Lead email verification — catch bounces before they burn your domain.

Bounces are the fastest way to lose a sending domain's reputation, so every
address gets checked *before* a campaign schedules it:

* **syntax**      — is it shaped like an email at all?
* **disposable**  — throwaway domains (mailinator & friends) always bounce later.
* **role**       — ``info@`` / ``contact@`` boxes deliver but rarely reply; flagged
  ``risky`` rather than rejected.
* **mx**          — does the domain publish MX records? Uses ``dnspython`` when
  installed, otherwise DNS-over-HTTPS (same zero-dep fallback as ``doctor``).

Verdicts: ``ok`` | ``risky`` | ``invalid``. The scheduler and the tick worker
skip ``invalid`` addresses; ``risky`` still sends but is visible in the tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

_DOH = "https://cloudflare-dns.com/dns-query"

_SYNTAX_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

_DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "yopmail.com",
    "tempmail.com", "temp-mail.org", "throwaway.email", "getnada.com",
    "sharklasers.com", "trashmail.com", "maildrop.cc", "dispostable.com",
    "mail-temporaire.fr", "jetable.org", "mytemp.email", "fakeinbox.com",
}

_ROLE_LOCALPARTS = {
    "info", "contact", "admin", "administrateur", "support", "sales",
    "postmaster", "webmaster", "abuse", "noreply", "no-reply", "office",
    "billing", "marketing", "newsletter", "bonjour",
}


@dataclass
class VerifyResult:
    email: str
    status: str = "ok"                     # ok | risky | invalid
    reasons: list[str] = field(default_factory=list)

    def _downgrade(self, to: str, reason: str) -> None:
        order = {"ok": 0, "risky": 1, "invalid": 2}
        if order[to] > order[self.status]:
            self.status = to
        self.reasons.append(reason)


def _mx_records(domain: str) -> list[str] | None:
    """MX hosts for *domain*; ``None`` means the lookup itself failed (don't
    penalize the lead for our network being down)."""
    try:
        import dns.resolver  # type: ignore

        try:
            return [str(r.exchange) for r in dns.resolver.resolve(domain, "MX")]
        except Exception:
            return []
    except ImportError:
        pass

    try:
        resp = requests.get(
            _DOH, params={"name": domain, "type": "MX"},
            headers={"accept": "application/dns-json"}, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    return [a.get("data", "") for a in data.get("Answer", []) if a.get("type") == 15]


def verify_email(email: str, *, check_mx: bool = True) -> VerifyResult:
    """Run all checks on one address. *check_mx=False* keeps it fully offline."""
    email = email.strip().lower()
    result = VerifyResult(email=email)

    if not _SYNTAX_RE.match(email):
        result._downgrade("invalid", "malformed address")
        return result

    local, _, domain = email.partition("@")

    if domain in _DISPOSABLE:
        result._downgrade("invalid", f"disposable domain ({domain})")
        return result

    if local in _ROLE_LOCALPARTS:
        result._downgrade("risky", f"role account ({local}@) — delivers but rarely replies")

    if check_mx:
        mx = _mx_records(domain)
        if mx is None:
            result._downgrade("risky", "MX lookup failed (network) — unverified")
        elif not mx:
            result._downgrade("invalid", f"domain {domain} has no MX records")

    return result
