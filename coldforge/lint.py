"""Pre-send content lint — the spam-filter review before any mail goes out.

Deliverability isn't only DNS (``doctor``): the copy itself trips filters.
This is the checklist a good SDR runs by eye, automated — spam-trigger words
(English *and* French), link count, shouting, length, punctuation. Deterministic
and offline, so it can run on every draft and inside CI.

Severities: ``block`` (very likely to filter), ``warn`` (hurts but survivable).
Score is 100 minus penalties — treat < 70 as "rewrite before sending".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Classic filter-bait, kept deliberately short and high-precision. One hit is a
# warn; the count compounds. English + French since campaigns run in both.
_SPAM_WORDS = {
    "act now", "buy now", "click here", "limited time", "no obligation",
    "risk-free", "risk free", "100% free", "guarantee", "guaranteed",
    "winner", "congratulations", "urgent", "cash", "cheap", "double your",
    "make money", "earn money", "special promotion", "exclusive deal",
    "once in a lifetime", "no credit card",
    # français
    "cliquez ici", "offre limitée", "gratuit", "garantie", "garanti",
    "promotion exclusive", "félicitations", "gagnez", "argent facile",
    "sans engagement", "profitez vite", "offre exceptionnelle", "incroyable",
}

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_CAPS_WORD_RE = re.compile(r"\b[A-ZÀ-Ü]{4,}\b")


@dataclass
class LintIssue:
    severity: str          # block | warn
    message: str


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def score(self) -> int:
        # A single "block" is enough to fall under the 70 send threshold.
        penalty = sum(35 if i.severity == "block" else 10 for i in self.issues)
        return max(0, 100 - penalty)

    @property
    def ok(self) -> bool:
        return self.score >= 70

    def _add(self, severity: str, message: str) -> None:
        self.issues.append(LintIssue(severity, message))


def lint_draft(subject: str, body: str) -> LintReport:
    """Lint one rendered email. Returns a :class:`LintReport` (never raises)."""
    report = LintReport()
    text = f"{subject}\n{body}"
    lower = text.lower()

    hits = sorted(w for w in _SPAM_WORDS if w in lower)
    if hits:
        sev = "block" if len(hits) >= 3 else "warn"
        report._add(sev, "spam-trigger words: " + ", ".join(hits[:6]))

    links = _URL_RE.findall(body)
    if len(links) > 1:
        report._add("warn", f"{len(links)} links — first-touch cold email survives best with 0–1")

    caps = [w for w in _CAPS_WORD_RE.findall(text) if w not in {"PDF", "SEO", "ROAS", "RGPD"}]
    if len(caps) >= 2:
        report._add("warn", "ALL-CAPS words read as shouting: " + ", ".join(caps[:5]))

    if text.count("!") > 1:
        report._add("warn", f"{text.count('!')} exclamation marks — keep at most one")

    words = len(body.split())
    if words > 150:
        report._add("warn", f"body is {words} words — replies drop sharply past ~120")
    elif words and words < 15:
        report._add("warn", f"body is only {words} words — too thin to earn a reply")

    if len(subject) > 60:
        report._add("warn", f"subject is {len(subject)} chars — gets truncated past ~60")
    if subject.isupper() and len(subject) > 3:
        report._add("block", "subject is all caps")
    if "{{" in text:
        report._add("block", "unfilled {{variables}} left in the email")

    return report
