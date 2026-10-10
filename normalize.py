"""
Deterministic cleanup of raw extracted fields — no LLM involved.

The extraction step deliberately keeps weight/date fields as raw text
("226.000 KG", "11-Jul-26") rather than asking the model to convert them,
because unit conversion and date parsing are things regular code does
reliably and an LLM can quietly get wrong. This module is where that
conversion actually happens.

This is general-purpose — it doesn't know anything about ACE or any
other destination form. It just turns messy raw text into clean typed
values any downstream mapping can use.
"""

import re
from datetime import datetime


def clean_description(raw_description: str) -> str:
    """
    Strips an HS/HTS code suffix that sometimes gets bundled into the
    description text itself, e.g. 'Ethernet Switch / HS:8517620020'
    -> 'Ethernet Switch'. The harmonized code is captured separately
    in harmonized_code, so it shouldn't also be duplicated here.
    """
    if not raw_description:
        return ""
    # Matches trailing "/ HS:1234567890", "HS: 1234567890",
    # "HTS 8517.62.0020", etc., with or without a leading slash
    cleaned = re.sub(
        r"\s*/?\s*H\.?T?S\.?:?\s*[\d.]+\s*$",
        "",
        raw_description,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def extract_weight_kg(raw_weight: str) -> float | None:
    """
    '226.000 KG' -> 226.0
    '300 lb'     -> converted to kg
    ''           -> None
    """
    if not raw_weight:
        return None

    match = re.search(r"([\d,]+\.?\d*)\s*(kg|kgs|lb|lbs)?", raw_weight, re.IGNORECASE)
    if not match:
        return None

    value = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "kg").lower()

    if unit in ("lb", "lbs"):
        value = value * 0.453592  # convert to kg

    return round(value, 3)


def parse_date_flexible(raw_date: str) -> datetime | None:
    """
    Tries a handful of common formats seen across shipping documents.
    Returns None if nothing matches, rather than guessing.
    """
    if not raw_date:
        return None

    raw_date = raw_date.strip()

    # Strip a trailing time component if present, e.g. "11-Jul-26 00:00"
    # -> "11-Jul-26" — we only care about the date part here.
    raw_date = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?$", "", raw_date)

    formats = [
        "%d-%b-%y",      # 11-Jul-26
        "%d-%b-%Y",      # 11-Jul-2026
        "%m/%d/%Y",      # 07/11/2026
        "%m/%d/%y",      # 07/11/26
        "%Y-%m-%d",      # 2026-07-11
        "%B %d, %Y",     # July 11, 2026
        "%m-%d-%y",      # 02-16-01 (legacy sample doc)
    ]

    for fmt in formats:
        try:
            return datetime.strptime(raw_date, fmt)
        except ValueError:
            continue

    return None


def normalize_fields(fields: dict) -> dict:
    """
    Takes the raw LLM extraction output and returns a copy with
    cleaned/typed values added alongside the originals. Doesn't
    delete the raw fields — useful to keep for debugging/audit trail.
    """
    normalized = dict(fields)

    # Copy each item dict before mutating it below (item["weight_kg"] = ...).
    # Without this, since `normalized = dict(fields)` is only a shallow
    # copy, mutating these in place would silently mutate the caller's
    # original `fields` dict too — the same cargo_line_items objects are
    # shared between the two.
    raw_line_items = [dict(item) for item in (fields.get("cargo_line_items", []) or [])]

    # The LLM doesn't always correctly tell a totals/summary row apart
    # from a real cargo line — a row with no description and no
    # harmonized code is almost certainly a totals row (e.g. "181, 14,
    # 4300" with nothing describing what it is), not real cargo. Treat
    # those as a detected total instead of a real line item, so they
    # don't get double-counted when we sum.
    real_items = []
    detected_total_kg = None

    for item in raw_line_items:
        description = (item.get("description") or "").strip()
        weight_kg = extract_weight_kg(item.get("weight", ""))

        if not description:
            if weight_kg is not None:
                detected_total_kg = weight_kg
            continue  # not a real cargo line — skip it

        item["weight_kg"] = weight_kg
        real_items.append(item)

    # Priority: an explicit total_weight field the model filled in >
    # a totals-looking row we detected above > summing the real items.
    explicit_total = extract_weight_kg(fields.get("total_weight", ""))
    if explicit_total is not None:
        total_weight_kg = explicit_total
    elif detected_total_kg is not None:
        total_weight_kg = detected_total_kg
    else:
        weights = [i["weight_kg"] for i in real_items if i.get("weight_kg") is not None]
        total_weight_kg = round(sum(weights), 3) if weights else None

    normalized["cargo_line_items"] = real_items
    normalized["weight_kg"] = total_weight_kg

    descriptions = [
        clean_description(item.get("description", ""))
        for item in real_items
        if item.get("description")
    ]
    normalized["combined_cargo_description"] = "; ".join(d for d in descriptions if d)

    doc_date = parse_date_flexible(fields.get("document_date", ""))
    normalized["document_date_parsed"] = doc_date.isoformat() if doc_date else None

    arrival_date = parse_date_flexible(fields.get("estimated_arrival_date", ""))
    normalized["estimated_arrival_date_parsed"] = (
        arrival_date.isoformat() if arrival_date else None
    )

    return normalized