"""Deterministic, opt-in website signals with a strict three-request budget."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import yaml

from .companies import Company

BOOKING_WORDS = (
    "calendly", "doctolib", "planity", "prendre rendez-vous", "prendre-rendez-vous",
    "réserver", "reservation", "booking", "book now", "appointment",
)
CAREER_WORDS = ("carrière", "carrieres", "carrières", "recrutement", "jobs", "careers", "hiring")
CONTACT_WORDS = ("contact", "contactez-nous", "get in touch", "nous contacter")


def _present(needles: tuple[str, ...], html: str) -> bool:
    lower = unescape(html).casefold()
    return any(word.casefold() in lower for word in needles)


def detect_no_website(html: str | None, site: str) -> tuple[str, str] | None:
    return ("true", site or "no website supplied") if not site or html is None else None


def detect_no_booking(html: str, site: str) -> tuple[str, str] | None:
    return None if _present(BOOKING_WORDS, html) else ("true", site)


def detect_no_schema(html: str, site: str) -> tuple[str, str] | None:
    pattern = r'<script[^>]+type=["\']application/ld\+json["\']'
    has_json_ld = bool(re.search(pattern, html, re.IGNORECASE))
    return None if has_json_ld else ("true", site)


def detect_blog_stale(html: str, site: str) -> tuple[str, str] | None:
    """Report an absent blog date, or the newest visible ISO date older than 12 months."""
    dates: list[date] = []
    pattern = r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"
    for year, month, day in re.findall(pattern, html):
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            pass
    if not dates:
        return "absent", site
    newest = max(dates)
    today = datetime.now(timezone.utc).date()
    months = (today.year - newest.year) * 12 + today.month - newest.month
    return (newest.isoformat(), site) if months > 12 else None


def detect_tech_stack(html: str, site: str) -> tuple[str, str] | None:
    signatures = {
        "WordPress": ("wp-content", "wp-includes", "wordpress"),
        "Wix": ("wixstatic.com", "wix.com"),
        "Shopify": ("cdn.shopify.com", "shopify-section"),
        "Webflow": ("webflow.css", "data-wf-page", "webflow.io"),
    }
    found = [name for name, marks in signatures.items() if _present(marks, html)]
    return (", ".join(found), site) if found else None


def detect_blocks_ai(html: str, site: str) -> tuple[str, str] | None:
    blocked: list[str] = []
    current_agents: list[str] = []
    wanted = {"gptbot": "GPTBot", "claudebot": "ClaudeBot", "perplexitybot": "PerplexityBot"}
    for raw in html.splitlines():
        line = raw.split("#", 1)[0].strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key.strip().casefold() == "user-agent":
            current_agents = [value.strip().casefold()]
        elif key.strip().casefold() == "disallow" and value.strip() and current_agents:
            for agent in current_agents:
                if agent in wanted and wanted[agent] not in blocked:
                    blocked.append(wanted[agent])
    return (", ".join(blocked), site) if blocked else None


def detect_google_ads(html: str, site: str) -> tuple[str, str] | None:
    match = re.search(r"\bAW-\d+\b|googleadservices\.com", html, re.IGNORECASE)
    return (match.group(0), site) if match else None


def detect_hiring(html: str, site: str) -> tuple[str, str] | None:
    return ("true", site) if _present(CAREER_WORDS, html) else None


def detect_contact_form(html: str, site: str) -> tuple[str, str] | None:
    for href, label in re.findall(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL
    ):
        text = re.sub(r"<[^>]+>", " ", label)
        if _present(CONTACT_WORDS, f"{href} {text}"):
            url = urljoin(site, href)
            return url, url
    if re.search(r"<form\b", html, re.IGNORECASE) and _present(CONTACT_WORDS, html):
        return site, site
    return None


def _definition(key: str, label: str, detector) -> dict[str, object]:
    return {"key": key, "label": label, "cost": "free", "detect": detector}


CATALOG: dict[str, dict[str, object]] = {
    "NO_WEBSITE": _definition("NO_WEBSITE", "No website", detect_no_website),
    "NO_ONLINE_BOOKING": _definition(
        "NO_ONLINE_BOOKING", "No online booking", detect_no_booking
    ),
    "NO_SCHEMA_MARKUP": _definition(
        "NO_SCHEMA_MARKUP", "No JSON-LD schema", detect_no_schema
    ),
    "BLOG_STALE": _definition("BLOG_STALE", "Blog stale or absent", detect_blog_stale),
    "TECH_STACK": _definition("TECH_STACK", "Detected technology", detect_tech_stack),
    "BLOCKS_AI_CRAWLERS": _definition(
        "BLOCKS_AI_CRAWLERS", "Blocks AI crawlers", detect_blocks_ai
    ),
    "RUNNING_GOOGLE_ADS": _definition(
        "RUNNING_GOOGLE_ADS", "Google Ads tag", detect_google_ads
    ),
    "HIRING_ROLE": _definition("HIRING_ROLE", "Hiring page or role", detect_hiring),
    "CONTACT_FORM_URL": _definition(
        "CONTACT_FORM_URL", "Contact form", detect_contact_form
    ),
}
SIGNALS = CATALOG


def confirmation_path(home: Path | None = None) -> Path:
    return (home or Path.home() / ".coldforge") / "signals.yaml"


def confirmed_signals(home: Path | None = None) -> set[str]:
    path = confirmation_path(home)
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    values = data.get("confirmed", []) if isinstance(data, dict) else data
    return {str(value) for value in values if str(value) in CATALOG}


def confirm(keys: list[str], home: Path | None = None) -> set[str]:
    selected = set(CATALOG) if any(key.casefold() == "free" for key in keys) else set(keys)
    unknown = selected - CATALOG.keys()
    if unknown:
        raise ValueError("unknown signals: " + ", ".join(sorted(unknown)))
    selected |= confirmed_signals(home)
    path = confirmation_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"confirmed": sorted(selected)}), encoding="utf-8")
    return selected


def profile_company(company: Company, *, fetch, confirmed: set[str]) -> list[tuple[str, str, str]]:
    """Collect confirmed signals using at most home, robots and one useful page."""
    active = confirmed & CATALOG.keys()
    if not active:
        return []
    site = _site_url(company)
    if not site:
        return [("NO_WEBSITE", "true", "no website supplied")] if "NO_WEBSITE" in active else []
    home_html = fetch(site)
    if home_html is None:
        return [("NO_WEBSITE", "true", site)] if "NO_WEBSITE" in active else []
    robots_url = urljoin(site.rstrip("/") + "/", "robots.txt")
    robots = fetch(robots_url) or ""
    useful_url = _useful_link(home_html, site)
    useful_html = ""
    if useful_url and _robots_allows(robots, useful_url):
        useful_html = fetch(useful_url) or ""
    combined = f"{home_html}\n{useful_html}"
    observations: list[tuple[str, str, str]] = []
    for key in sorted(active):
        detector = CATALOG[key]["detect"]
        source = robots if key == "BLOCKS_AI_CRAWLERS" else combined
        evidence_site = robots_url if key == "BLOCKS_AI_CRAWLERS" else site
        result = detector(source, evidence_site)  # type: ignore[operator]
        if result:
            observations.append((key, result[0], result[1]))
    return observations


def _site_url(company: Company) -> str:
    value = company.website or company.domain
    if not value:
        return ""
    return value if "://" in value else f"https://{value}"


def _useful_link(html: str, site: str) -> str:
    for href, label in re.findall(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL
    ):
        text = re.sub(r"<[^>]+>", " ", label)
        useful_words = CAREER_WORDS + CONTACT_WORDS + ("blog", "actualités", "news")
        if _present(useful_words, f"{href} {text}"):
            return urljoin(site, href)
    return ""


def _robots_allows(robots: str, url: str) -> bool:
    path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
    applies = False
    for raw in robots.splitlines():
        key, sep, value = raw.split("#", 1)[0].partition(":")
        if not sep:
            continue
        if key.strip().casefold() == "user-agent":
            applies = value.strip() == "*" or value.strip().casefold() == "coldforgebot"
        elif applies and key.strip().casefold() == "disallow":
            rule = value.strip()
            if rule and path.startswith(rule):
                return False
    return True
