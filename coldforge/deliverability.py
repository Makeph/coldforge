"""Domain deliverability checks — SPF / DKIM / DMARC.

Inspired by coldflow's `dmarc-doctor`: before you send a single cold email,
confirm the sending domain is actually authenticated, or you'll land in spam.

Uses ``dnspython`` when installed, otherwise DNS-over-HTTPS (Cloudflare) so the
check still works with zero extra dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

_DOH = "https://cloudflare-dns.com/dns-query"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class DeliverabilityReport:
    domain: str
    checks: list[Check]

    @property
    def score(self) -> int:
        """0–100, weighted: SPF 30, DMARC 40, DKIM 30."""
        weights = {"SPF": 30, "DMARC": 40, "DKIM": 30}
        return sum(weights.get(c.name, 0) for c in self.checks if c.ok)

    @property
    def verdict(self) -> str:
        s = self.score
        if s >= 90:
            return "ready"
        if s >= 60:
            return "needs work"
        return "not ready — mail will likely land in spam"


def _txt_records(name: str) -> list[str]:
    """Return TXT records for *name* via dnspython or DoH fallback."""
    try:
        import dns.resolver  # type: ignore

        try:
            answers = dns.resolver.resolve(name, "TXT")
            return ["".join(s.decode() if isinstance(s, bytes) else s
                            for s in r.strings) for r in answers]
        except Exception:
            return []
    except ImportError:
        pass

    try:
        resp = requests.get(
            _DOH, params={"name": name, "type": "TXT"},
            headers={"accept": "application/dns-json"}, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for ans in data.get("Answer", []):
        if ans.get("type") == 16:  # TXT
            out.append(ans.get("data", "").strip('"'))
    return out


def check_domain(domain: str) -> DeliverabilityReport:
    domain = domain.strip().lower().lstrip("@")
    checks: list[Check] = []

    # SPF
    spf = [t for t in _txt_records(domain) if t.lower().startswith("v=spf1")]
    checks.append(Check(
        "SPF", bool(spf),
        spf[0] if spf else "No v=spf1 TXT record found.",
        fix="Add a TXT record: v=spf1 include:<your-esp> -all",
    ))

    # DMARC
    dmarc = [t for t in _txt_records(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    has_policy = any("p=" in t and "p=none" not in t.lower() for t in dmarc)
    checks.append(Check(
        "DMARC", bool(dmarc),
        (dmarc[0] if dmarc else "No _dmarc TXT record found.")
        + ("" if has_policy or not dmarc else "  (policy is p=none — monitoring only)"),
        fix="Add TXT at _dmarc.<domain>: v=DMARC1; p=quarantine; rua=mailto:dmarc@<domain>",
    ))

    # DKIM — we can't know the selector, so probe the common ones.
    dkim_found = ""
    for selector in ("google", "default", "selector1", "selector2", "k1", "mail", "dkim"):
        recs = _txt_records(f"{selector}._domainkey.{domain}")
        if any("v=dkim1" in r.lower() or "p=" in r for r in recs):
            dkim_found = selector
            break
    checks.append(Check(
        "DKIM", bool(dkim_found),
        f"Found selector '{dkim_found}'." if dkim_found
        else "No DKIM record at common selectors (google, default, selector1/2, k1, mail).",
        fix="Enable DKIM signing in your email provider and publish the selector it gives you.",
    ))

    return DeliverabilityReport(domain=domain, checks=checks)
