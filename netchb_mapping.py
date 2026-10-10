"""
Maps normalized, general-purpose extraction output to real NetCHB form
field IDs, ready for a content script to inject via
document.getElementById(id).value = ...

This is the NetCHB-specific counterpart to ace_mapping.py. Extraction
and normalization stay generic; this file is where NetCHB's actual DOM
structure and quirks live.

Transport/identifier decisions come from identifiers.py (deterministic,
client-confirmed rules) rather than from the LLM's guess:
  * ocean vs air: decided by the master number (4 letters = ocean,
    3 digits = air); the LLM's mode text is only a fallback
  * Master B/L: the candidate that matches the ocean rule, never the house
    number and never a forwarder file number
  * Master SCAC: first 4 letters of that master B/L
  * Mode: air = 40; ocean = 11 (FCL) or 10 (LCL), blank when load type unknown
"""

from datetime import datetime

from identifiers import (
    check_mode_consistency,
    ocean_mode_code,
    pick_master_number,
    scac_from_master,
    split_vessel_and_voyage,
    transport_family,
)

AIR_MODE = "40"

# Carrier code lookups apply to AIR only (IATA). For ocean, the Master SCAC
# comes from the master B/L prefix. The ocean Carrier Code (cCode) rule is an
# open client question (same as master SCAC, or the vessel operator?), so it
# is left blank rather than guessed.
CARRIER_NAME_TO_IATA_CODE = {
    "CHINA SOUTHERN AIRLINES": "CZ",
    "CHINA AIRLINES": "CI",
    "CHINA EASTERN AIRLINES": "MU",
    "AIR CHINA": "CA",
    "CATHAY PACIFIC": "CX",
    "KOREAN AIR": "KE",
    "EVA AIR": "BR",
    "UNITED AIRLINES": "UA",
    "DELTA AIR LINES": "DL",
    "FEDEX": "FX",
    "UPS": "5X",
}


def map_air_carrier_code(carrier_name: str) -> str:
    if not carrier_name:
        return ""
    return CARRIER_NAME_TO_IATA_CODE.get(carrier_name.strip().upper(), "")


def split_flight_or_voyage(raw: str) -> tuple:
    """
    Air: '2513' alone, or 'CZ2513' -> just the number part '2513'.
    Falls back to the raw value if it doesn't look like carrier+number.
    (Ocean vessel/voyage strings go through identifiers.split_vessel_and_voyage.)
    """
    if not raw:
        return "", ""
    import re
    m = re.match(r"^([A-Z]{2})\s*(\d+)$", raw.strip().upper())
    if m:
        return m.group(1), m.group(2)
    return "", raw.strip()


def resolve_transport(normalized: dict) -> dict:
    """
    Decide family (air/ocean), the ocean master B/L, and the NetCHB mode
    code. Single source of truth for both the field mapping and warnings.
    """
    ocean_master = pick_master_number(
        [normalized.get("master_bol_number"), normalized.get("ams_bol_number")],
        house=normalized.get("house_bol_number"),
    )
    number = ocean_master or normalized.get("mawb_number") or ""
    family = transport_family(number, normalized.get("mode_of_transport", ""))

    if family == "air":
        mode_code = AIR_MODE
    elif family == "ocean":
        mode_code = ocean_mode_code(normalized.get("service_type")) or ""
    else:
        mode_code = ""

    return {
        "family": family,
        "ocean_master": ocean_master,
        "mode_code": mode_code,
        "number_checked": number,
    }


def netchb_warnings(normalized: dict) -> list[str]:
    """
    Human-readable problems the user should look at before filling.
    Kept separate from map_to_netchb_fields() so the {id: value} dict stays
    clean for the content script.
    """
    t = resolve_transport(normalized)
    warnings = []

    ok, reason = check_mode_consistency(
        normalized.get("mode_of_transport", ""), t["number_checked"]
    )
    if not ok:
        warnings.append(f"Mode conflict: {reason}. Used the master number.")

    if t["family"] is None:
        warnings.append(
            "Could not tell air from ocean; Mode of Transportation left blank."
        )
    elif t["family"] == "ocean":
        if not t["ocean_master"]:
            warnings.append(
                "No valid ocean master B/L found (expects 4 leading letters, "
                "and not the house number); Master B/L and SCAC left blank."
            )
        if not t["mode_code"]:
            warnings.append(
                "Ocean load type unclear (FCL vs LCL); choose mode 10 or 11 "
                "manually."
            )
    return warnings


def map_to_netchb_fields(normalized: dict) -> dict:
    """
    Returns {field_id: value} pairs matching NetCHB's real DOM ids,
    ready for direct injection: document.getElementById(id).value = v
    """
    t = resolve_transport(normalized)
    family, mode_code, ocean_master = t["family"], t["mode_code"], t["ocean_master"]

    raw_voyage = normalized.get("flight_or_voyage_number", "")
    if family == "ocean":
        voyage = split_vessel_and_voyage(raw_voyage)[1]
    else:
        voyage = split_flight_or_voyage(raw_voyage)[1]

    carrier_code = (
        map_air_carrier_code(normalized.get("carrier_name", ""))
        if family == "air" else ""
    )

    weight_kg = normalized.get("weight_kg")
    gross_weight_whole = str(round(weight_kg)) if weight_kg is not None else ""

    def mmddyy(iso_date_str):
        if not iso_date_str:
            return ""
        return datetime.fromisoformat(iso_date_str).strftime("%m%d%y")

    fields = {
        "imp": normalized.get("shipper_name", ""),           # Importer of Record
        "ulc": normalized.get("consignee_name", ""),         # Ultimate Consignee
        "mot": mode_code,                                     # Mode of Transportation
        "grw": gross_weight_whole,                            # Gross Weight
        "desc": normalized.get("combined_cargo_description", ""),  # Description
        "locGoods": normalized.get("location_of_goods_code", ""),  # Location of Goods
        "cCode": carrier_code,                                # Carrier Code (air only)
        "vNo": voyage,                                        # Voyage/Flight No
        # Date of Import: still taken from the document's own date. Which date
        # the client wants here is an open question (see SKILL.md).
        "dImp": mmddyy(normalized.get("document_date_parsed")),
        "eArrive": mmddyy(normalized.get("estimated_arrival_date_parsed")),
        # "pEntry" (Port of Entry) intentionally omitted — this isn't
        # something that comes from a shipping document; it's tied to
        # the broker's own filing setup.
    }

    # Master SCAC / Master B/L only apply to ocean shipments. They are keyed
    # on family rather than mode so the values are still produced when the
    # load type is unknown and mot is left for the user to choose.
    # NOTE: NetCHB only shows these inputs after mot is an ocean mode, so if
    # the fill reports scac0/mb0 as "not found", set mot and click Fill again.
    if family == "ocean":
        fields["scac0"] = scac_from_master(ocean_master)
        fields["mb0"] = ocean_master or ""

    return fields