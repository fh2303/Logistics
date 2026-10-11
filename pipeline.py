"""
Normalize + map a raw extraction (or a merged shipment). Cheap and
deterministic, so it runs fresh on every request and is never cached.
"""

from normalize import normalize_fields
from ace_mapping import map_to_ace_fields
from netchb_mapping import map_to_netchb_fields, netchb_warnings


def build_response(fields: dict, target: str) -> dict:
    normalized = normalize_fields(fields)

    result = {"extracted_fields": fields}
    if target in ("ace", "both"):
        result["ace_fields"] = map_to_ace_fields(normalized)
    if target in ("netchb", "both"):
        result["netchb_fields"] = map_to_netchb_fields(normalized)
        result["warnings"] = netchb_warnings(normalized)
    return result