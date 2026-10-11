"""
Merge the per-file extractions of ONE shipment into a single field dict.

Documents can arrive in separate emails and over time, so this is a pure
function: give it everything attached to the shipment so far and it
recomputes the result. No LLM, no database, no side effects. Each file's
extraction is cached separately (by file hash); the merge is cheap and is
simply re-run whenever a document is added.

Input:   [{"name": "ex1.pdf", "fields": <raw extraction dict>}, ...]
Output:  {
           "fields":    same shape as one extraction, so it feeds
                        normalize_fields() / map_to_netchb_fields() unchanged,
           "conflicts": disagreements worth a human look,
           "sources":   which document each merged field came from,
           "documents": what was merged (name + detected document type),
         }

Rules (all editable in the tables below — they are DRAFTS based on the
sample documents and client answers, not confirmed policy):
  * Priority is per FIELD, not per document type. HTS lives on the ISF in one
    shipment and on the invoice in another.
  * Newest version of the same document type wins (by prepared_date, falling
    back to the order the files were added). When two versions disagree, that
    is reported as a "version_change", never silently dropped.
  * The master B/L is chosen with identifiers.py's rule (4 leading letters,
    never the house number), not by trusting a label. No valid master => "".
  * Disagreements are detected on normalized values, so "WONTON FOOD
    CORPORATION(TX)" vs "Wonton Food Corp." is NOT a conflict, but ETA 09-10 vs
    06-SEP is.
"""

import re

from identifiers import normalize_number, pick_master_number
from normalize import extract_weight_kg, parse_date_flexible

# ---------------------------------------------------------------------------
# Editable policy
# ---------------------------------------------------------------------------

DEFAULT_PRIORITY = ["arrival_notice", "bol", "isf", "invoice", "packing_list", "other"]

# Which document types to trust first for a field. Unlisted types are tried
# afterwards in DEFAULT_PRIORITY order.
FIELD_PRIORITY = {
    # Carrier-issued documents first for the master; the forwarder's notice
    # often mislabels its own file number as "master".
    "master_bol_number": ["bol", "isf", "arrival_notice"],
    "house_bol_number": ["arrival_notice", "bol", "isf"],
    "ams_bol_number": ["arrival_notice", "bol", "isf"],
    "mawb_number": ["bol", "arrival_notice", "isf"],
    "hawb_number": ["bol", "arrival_notice", "isf"],
    "shipper_name": ["bol", "arrival_notice", "isf", "invoice", "packing_list"],
    # Invoice/packing list name the buyer, sometimes with a different address.
    "consignee_name": ["arrival_notice", "bol", "isf"],
    "carrier_name": ["arrival_notice", "bol", "isf"],
    "flight_or_voyage_number": ["arrival_notice", "isf", "bol"],
    "service_type": ["arrival_notice", "bol", "isf"],
    "location_of_goods_code": ["arrival_notice"],
    # The ISF is filed before sailing, so its ETA is a forecast.
    "estimated_arrival_date": ["arrival_notice", "isf"],
    # Beware: a packing list's NET weight can be mistaken for the total.
    "total_weight": ["arrival_notice", "bol", "packing_list"],
    "hts_code": ["isf", "invoice"],
    # Invoices repeat product lines, which would duplicate the description.
    "cargo_line_items": ["arrival_notice", "bol", "isf", "packing_list", "invoice"],
}

# Only disagreements on these fields are reported (the ones that end up on the
# NetCHB form or decide it). Address/port formatting differs too much between
# documents to compare usefully.
NAME_FIELDS = {"shipper_name", "consignee_name", "carrier_name"}
ID_FIELDS = {"master_bol_number", "ams_bol_number", "house_bol_number",
             "mawb_number", "hawb_number"}
DATE_FIELDS = {"estimated_arrival_date"}
WEIGHT_FIELDS = {"total_weight"}
CONFLICT_FIELDS = (NAME_FIELDS | ID_FIELDS | DATE_FIELDS | WEIGHT_FIELDS |
                   {"flight_or_voyage_number", "service_type",
                    "location_of_goods_code", "hts_code", "mode_of_transport"})

# Fields handled by dedicated logic rather than the generic scalar rule.
_SPECIAL = {"cargo_line_items", "container_numbers", "master_bol_number",
            "house_bol_number", "document_type"}

_LEGAL_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company",
                "ltd", "limited", "llc", "lp", "llp", "plc", "gmbh", "sa"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify_document(document_type) -> str:
    """Map the LLM's free-text document_type to a category used by the tables."""
    t = (document_type or "").lower()
    if "arrival" in t:           # check first: "Arrival Notice / Freight Invoice"
        return "arrival_notice"
    if re.search(r"\bisf\b", t) or "importer security" in t:
        return "isf"
    if "packing" in t:
        return "packing_list"
    if "invoice" in t:
        return "invoice"
    if re.search(r"lading|waybill|\bawb\b|\bbol\b|b/l", t):
        return "bol"
    return "other"


def _val(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return ""


def _text_key(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _name_key(s: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", s.lower())   # drop "(TX)", "(GUANGZHOU)"
    return " ".join(t for t in re.findall(r"[a-z0-9]+", s) if t not in _LEGAL_WORDS)


def _date_key(s: str) -> str:
    dt = parse_date_flexible(s)
    return dt.date().isoformat() if dt else _text_key(s)


def _compact(s: str) -> str:
    return _text_key(s).replace(" ", "")

def _same(field: str, a: str, b: str) -> bool:
    if field in NAME_FIELDS:
        return _name_key(a).replace(" ", "") == _name_key(b).replace(" ", "")
    if field in ID_FIELDS:
        return normalize_number(a) == normalize_number(b)
    if field in DATE_FIELDS:
        return _date_key(a) == _date_key(b)
    if field in WEIGHT_FIELDS:
        wa, wb = extract_weight_kg(a), extract_weight_kg(b)
        if wa is None or wb is None:
            return _compact(a) == _compact(b)
        return abs(wa - wb) <= max(0.5, 0.005 * max(wa, wb))
    return _compact(a) == _compact(b)


def _prepare(documents) -> list[dict]:
    from datetime import datetime
    epoch = datetime(2000, 1, 1)
    prepared = []
    for i, d in enumerate(documents or []):
        fields = d.get("fields") or {}
        prepared_dt = parse_date_flexible(_val(fields.get("prepared_date")))
        prepared.append({
            "name": d.get("name") or f"document {i + 1}",
            "category": classify_document(fields.get("document_type")),
            "fields": fields,
            "index": i,
            "ts": (prepared_dt - epoch).total_seconds() if prepared_dt else 0.0,
        })
    return prepared


def _order_docs(docs: list[dict], field: str) -> list[dict]:
    """Best source first: field priority, then newest version, then latest added."""
    listed = FIELD_PRIORITY.get(field, DEFAULT_PRIORITY)
    full = listed + [c for c in DEFAULT_PRIORITY if c not in listed]
    rank = {c: i for i, c in enumerate(full)}
    return sorted(docs, key=lambda d: (rank[d["category"]], -d["ts"], -d["index"]))


def _conflict(field, chosen_doc, chosen_value, others) -> dict:
    same_type = all(d["category"] == chosen_doc["category"] for d, _ in others)
    return {
        "field": field,
        "kind": "version_change" if same_type else "cross_document",
        "chosen": {"value": chosen_value, "source": chosen_doc["name"]},
        "others": [{"value": v, "source": d["name"]} for d, v in others],
    }


def _resolve_scalar(field: str, docs: list[dict]):
    candidates = [(d, _val(d["fields"].get(field))) for d in _order_docs(docs, field)]
    candidates = [(d, v) for d, v in candidates if v]
    if not candidates:
        return "", None, None
    chosen_doc, chosen = candidates[0]
    conflict = None
    if field in CONFLICT_FIELDS:
        others = [(d, v) for d, v in candidates[1:] if not _same(field, chosen, v)]
        if others:
            conflict = _conflict(field, chosen_doc, chosen, others)
    return chosen, chosen_doc["name"], conflict


def _resolve_master(docs: list[dict], house: str):
    """First candidate (any doc, master or AMS field) that passes the ocean
    rule and isn't the house number. '' if none — blank beats wrong."""
    valid = []
    for d in _order_docs(docs, "master_bol_number"):
        for key in ("master_bol_number", "ams_bol_number"):
            n = pick_master_number([d["fields"].get(key)], house=house)
            if n:
                valid.append((d, n))
    if not valid:
        return "", None, None
    chosen_doc, chosen = valid[0]
    others = [(d, n) for d, n in valid[1:] if n != chosen]
    conflict = _conflict("master_bol_number", chosen_doc, chosen, others) if others else None
    return chosen, chosen_doc["name"], conflict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def merge_shipment_fields(documents) -> dict:
    docs = _prepare(documents)
    merged, sources, conflicts = {}, {}, []

    def record(field, value, source, conflict):
        merged[field] = value
        if source:
            sources[field] = source
        if conflict:
            conflicts.append(conflict)

    # House first: the master resolver needs it to exclude it.
    house, src, conf = _resolve_scalar("house_bol_number", docs)
    record("house_bol_number", house, src, conf)

    master, src, conf = _resolve_master(docs, house)
    record("master_bol_number", master, src, conf)

    all_keys = sorted({k for d in docs for k in d["fields"]} - _SPECIAL)
    for field in all_keys:
        value, src, conf = _resolve_scalar(field, docs)
        if value:
            record(field, value, src, conf)

    # Line items: take ONE document's list wholesale (avoids double counting).
    items, items_src = [], None
    for d in _order_docs(docs, "cargo_line_items"):
        candidate = d["fields"].get("cargo_line_items") or []
        if candidate:
            items, items_src = [dict(i) for i in candidate], d["name"]
            break
    merged["cargo_line_items"] = items
    if items_src:
        sources["cargo_line_items"] = items_src

    # Containers: union, deduplicated, original order.
    seen, containers = set(), []
    for d in docs:
        for c in d["fields"].get("container_numbers") or []:
            key = normalize_number(c)
            if key and key not in seen:
                seen.add(key)
                containers.append(key)
    merged["container_numbers"] = containers

    categories = sorted({d["category"] for d in docs})
    merged["document_type"] = "Merged shipment" + (f" ({', '.join(categories)})" if categories else "")

    return {
        "fields": merged,
        "conflicts": conflicts,
        "sources": sources,
        "documents": [{"name": d["name"], "category": d["category"]} for d in docs],
    }