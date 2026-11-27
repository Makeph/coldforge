"""Select and export explainable company cohorts from stored signals."""

from __future__ import annotations

import csv
from pathlib import Path

from .companies import Company
from .db import Store


def cohort(
    store: Store,
    must: list[str] | None = None,
    none_of: list[str] | None = None,
    ranges: dict[str, tuple[float | None, float | None]] | None = None,
) -> list[Company]:
    required = set(must or [])
    forbidden = set(none_of or [])
    bounded = ranges or {}
    selected: list[Company] = []
    for company in store.list_companies():
        if company.id is None:
            continue
        signals = store.signals_of(company.id)
        if not required.issubset(signals):
            continue
        if forbidden.intersection(signals):
            continue
        if not _within_ranges(signals, bounded):
            continue
        selected.append(company)
    return selected


def count(store: Store, **criteria) -> int:
    return len(cohort(store, **criteria))


def inclusion_reasons(
    store: Store,
    company: Company,
    must: list[str] | None = None,
    ranges: dict[str, tuple[float | None, float | None]] | None = None,
) -> list[str]:
    signals = store.signals_of(company.id) if company.id is not None else {}
    reasons = [f"{key}={signals[key][0]}" for key in must or [] if key in signals]
    reasons.extend(
        f"{key}={signals[key][0]} in [{lo if lo is not None else '-∞'}, "
        f"{hi if hi is not None else '∞'}]"
        for key, (lo, hi) in (ranges or {}).items() if key in signals
    )
    return reasons or ["matches cohort criteria"]


def export_csv(companies: list[Company], path: str | Path) -> int:
    """Write a list-only sequencer input. This function never sends anything."""
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("name", "domain", "website", "city", "category", "phone", "source"),
        )
        writer.writeheader()
        for company in companies:
            writer.writerow({key: getattr(company, key) for key in writer.fieldnames})
    return len(companies)


def _within_ranges(
    signals: dict[str, tuple[str, str]],
    ranges: dict[str, tuple[float | None, float | None]],
) -> bool:
    for key, (lo, hi) in ranges.items():
        if key not in signals:
            return False
        try:
            value = float(signals[key][0])
        except ValueError:
            return False
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
    return True
