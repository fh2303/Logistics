"""
Shipment logic with no web framework in it, so it can be tested directly.
api.py only wraps these functions in HTTP routes.

A shipment is an accumulating set of documents. Whenever the view is built,
the merge is recomputed from everything attached so far — cheap, no LLM.
"""

import database as db
from merge import merge_shipment_fields
from pipeline import build_response


def _unique_names(rows: list[dict]) -> list[str]:
    """Filenames repeat across emails ('invoice.pdf'); the merge reports
    sources by name, so disambiguate duplicates with the document id."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["filename"] or ""] = counts.get(r["filename"] or "", 0) + 1
    names = []
    for r in rows:
        base = r["filename"] or f"document {r['id']}"
        names.append(f"{base} #{r['id']}" if counts[r["filename"] or ""] > 1 else base)
    return names


def build_shipment_view(shipment_id: int, target: str = "netchb") -> dict | None:
    """Everything the UI needs for one shipment, or None if it doesn't exist."""
    shipment = db.get_shipment(shipment_id)
    if shipment is None:
        return None

    rows = db.list_shipment_documents(shipment_id)
    names = _unique_names(rows)
    merged = merge_shipment_fields(
        [{"name": n, "fields": r["fields"]} for n, r in zip(names, rows)]
    )

    view = {
        "shipment": shipment,
        "documents": [
            {
                "id": r["id"],
                "name": n,
                "category": m["category"],
                "mode": r["mode"],
                "extraction_version": r["extraction_version"],
                "added_at": r["added_at"],
            }
            for r, n, m in zip(rows, names, merged["documents"])
        ],
        "merged_fields": merged["fields"],
        "conflicts": merged["conflicts"],
        "sources": merged["sources"],
    }

    if not rows:
        # Nothing to map yet; say so instead of showing "could not tell air
        # from ocean" style warnings for an empty shipment.
        if target in ("ace", "both"):
            view["ace_fields"] = {}
        if target in ("netchb", "both"):
            view["netchb_fields"] = {}
            view["warnings"] = ["No documents attached yet."]
        return view

    mapped = build_response(merged["fields"], target)
    mapped.pop("extracted_fields")  # already present as merged_fields
    view.update(mapped)
    return view


def attach_extraction(
    shipment_id: int,
    file_hash: str,
    filename: str | None,
    mode: str,
    extraction_version: int,
    fields: dict,
) -> tuple[int, bool]:
    """Attach a finished extraction to a shipment. (document_id, created)."""
    return db.add_shipment_document(
        shipment_id, file_hash, filename, mode, extraction_version, fields
    )