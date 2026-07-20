"""Shared, deterministic source normalization helpers."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

EMPTY_MARKERS = {"", "{}", "*None"}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return None if cleaned in EMPTY_MARKERS else cleaned


def normalize_name(value: str | None) -> str:
    cleaned = clean_text(value) or ""
    return re.sub(r"[^a-z0-9]+", " ", cleaned.casefold()).strip()


def parse_bool(value: str | None) -> bool | None:
    cleaned = (clean_text(value) or "").casefold()
    if cleaned in {"yes", "y", "true", "1"}:
        return True
    if cleaned in {"no", "n", "false", "0"}:
        return False
    return None


def parse_decimal(value: str | None) -> Decimal | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    cleaned = cleaned.replace("$", "").replace(",", "").replace("%", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(value: str | None) -> date | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def is_control_item_number(value: str | None) -> bool:
    """Hide slash/control items from planning while retaining transaction detail."""

    cleaned = clean_text(value)
    return bool(cleaned and cleaned.startswith(("/", "\\")))


def is_cover_order(journal_memo: str | None) -> bool:
    cleaned = clean_text(journal_memo)
    if cleaned is None:
        return False
    return bool(re.search(r"(?:^|\s|-|;)COVER\s+ORDER\s*$", cleaned, flags=re.IGNORECASE))
