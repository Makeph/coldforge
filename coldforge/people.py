"""Find public company decision makers without social-network scraping."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin

from .companies import Company

PUBLIC_PATHS = ("/equipe", "/a-propos", "/about", "/team", "/mentions-legales")
TITLE_WORDS = (
    "gérant", "gérante", "gerant", "gerante", "dirigeant", "dirigeante",
    "fondateur", "fondatrice",
    "directeur", "directrice", "président", "president", "owner", "founder",
    "chief executive officer", "ceo", "managing director",
)
_NAME = r"([A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]+(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]+){1,3})"


@dataclass
class Person:
    first_name: str
    last_name: str
    title: str
    source_url: str
    email: str = ""

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class EmailPattern(str):
    """A string-compatible pattern that also carries its inferred domain."""

    domain: str

    def __new__(cls, value: str, domain: str):
        instance = super().__new__(cls, value)
        instance.domain = domain
        return instance


def find_people(company: Company, *, fetch) -> list[Person]:
    """Return at most two named decision makers from the company's own pages."""
    base = company.website or (f"https://{company.domain}" if company.domain else "")
    if not base:
        return []
    people: list[Person] = []
    seen: set[str] = set()
    for path in PUBLIC_PATHS:
        url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        html = fetch(url)
        if not html:
            continue
        for person in extract_people(html, url):
            identity = person.name.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            people.append(person)
            if len(people) == 2:
                return people
    return people


def extract_people(html: str, source_url: str) -> list[Person]:
    # Keep an explicit boundary between HTML elements so one person's title
    # cannot be greedily joined to the next person's name.
    text = re.sub(r"<[^>]+>", " | ", unescape(html))
    text = re.sub(r"\s+", " ", text).strip()
    title_re = "|".join(re.escape(word) for word in sorted(TITLE_WORDS, key=len, reverse=True))
    patterns = (
        rf"{_NAME}\s*[,–—|:-]+\s*({title_re})\b",
        rf"\b({title_re})\s*[:–—|-]+\s*{_NAME}",
        rf"\b({title_re})\s+(?:de\s+(?:la\s+)?société\s+)?{_NAME}",
    )
    found: list[Person] = []
    for index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if index == 0:
                full_name, title = match.group(1), match.group(2)
            else:
                title, full_name = match.group(1), match.group(2)
            parts = full_name.strip().split()
            if len(parts) < 2:
                continue
            nearby = text[max(0, match.start() - 100):match.end() + 100]
            email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", nearby)
            found.append(Person(
                first_name=parts[0], last_name=" ".join(parts[1:]), title=title,
                source_url=source_url, email=email_match.group(0).lower() if email_match else "",
            ))
    unique: dict[str, Person] = {}
    for person in found:
        unique.setdefault(person.name.casefold(), person)
    return list(unique.values())[:2]


def guess_email_pattern(known_emails: list[str], domain: str) -> EmailPattern | None:
    """Infer a conservative pattern when local parts expose two name tokens."""
    domain = domain.strip().casefold().removeprefix("www.")
    locals_ = [
        local.casefold() for email in known_emails
        for local, separator, email_domain in [email.partition("@")]
        if separator and email_domain.casefold() == domain
    ]
    if not locals_:
        return None
    patterns = {
        "first.last": re.compile(r"^[a-z][a-z'-]+\.[a-z][a-z'-]+$"),
        "first_last": re.compile(r"^[a-z][a-z'-]+_[a-z][a-z'-]+$"),
        "first-last": re.compile(r"^[a-z][a-z'-]+-[a-z][a-z'-]+$"),
        "flast": re.compile(r"^[a-z][a-z'-]{2,}$"),
    }
    for name, regex in patterns.items():
        if all(regex.fullmatch(local) for local in locals_):
            return EmailPattern(name, domain)
    return None


def candidates(person: Person, pattern: str | None, domain: str = "") -> list[str]:
    """Build one candidate for a known pattern; never claim it is verified."""
    if person.email:
        return [person.email]
    domain = (domain or getattr(pattern, "domain", "")).strip().lower().removeprefix("www.")
    if not pattern or not domain:
        return []
    first = _slug(person.first_name)
    last = _slug(person.last_name).replace("-", "")
    local = {
        "first.last": f"{first}.{last}",
        "first_last": f"{first}_{last}",
        "first-last": f"{first}-{last}",
        "flast": f"{first[:1]}{last}",
        "first": first,
    }.get(pattern)
    return [f"{local}@{domain}"] if local else []


def _slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9-]", "", plain.casefold().replace("’", "-"))
