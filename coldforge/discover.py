"""Free company discovery from OpenStreetMap or a local CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import quote, urlencode

from .companies import Company, normalize_domain

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def estimate(source: str = "osm", **_: object) -> dict[str, object]:
    """Return the price estimate before discovery starts."""
    if source.lower() not in {"osm", "csv"}:
        raise ValueError(f"unknown discovery source: {source}")
    return {"source": source.lower(), "cost_eur": 0.0, "label": "0 €"}


def discover_osm(category: str, city: str, *, fetch_json) -> list[Company]:
    """Discover named businesses with Overpass; network access is injected."""
    query = (
        "[out:json][timeout:25];"
        f'area["name"="{_escape(city)}"]->.searchArea;'
        "("
        f'nwr["craft"="{_escape(category)}"](area.searchArea);'
        f'nwr["shop"="{_escape(category)}"](area.searchArea);'
        f'nwr["office"="{_escape(category)}"](area.searchArea);'
        f'nwr["amenity"="{_escape(category)}"](area.searchArea);'
        ");out tags center;"
    )
    url = f"{OVERPASS_URL}?{urlencode({'data': query}, quote_via=quote)}"
    payload = fetch_json(url)
    if isinstance(payload, str):
        payload = json.loads(payload)
    elements = payload.get("elements", []) if isinstance(payload, dict) else []
    found: list[Company] = []
    seen: set[str] = set()
    for item in elements:
        tags = item.get("tags", {}) if isinstance(item, dict) else {}
        name = str(tags.get("name", "")).strip()
        if not name:
            continue
        website = str(tags.get("website") or tags.get("contact:website") or "").strip()
        domain = normalize_domain(website)
        dedupe = domain or f"{name.casefold()}|{city.casefold()}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        found.append(Company(
            name=name, domain=domain, website=website, city=city, category=category,
            phone=str(tags.get("phone") or tags.get("contact:phone") or ""), source="osm",
        ))
    return found


def discover_csv(path: str | Path) -> list[Company]:
    """Load companies from a UTF-8 CSV with the documented five columns."""
    companies: list[Company] = []
    seen: set[str] = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            website = (row.get("website") or "").strip()
            domain = normalize_domain(website)
            city = (row.get("city") or "").strip()
            dedupe = domain or f"{name.casefold()}|{city.casefold()}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            companies.append(Company(
                name=name, domain=domain, website=website, city=city,
                category=(row.get("category") or "").strip(),
                phone=(row.get("phone") or "").strip(), source="csv",
            ))
    return companies


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
