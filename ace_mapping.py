"""
Maps normalized, general-purpose extraction output to CBP ACE Entry
Summary form field values.

This is the ONLY file that knows anything about ACE specifically. If
you ever need to support a different destination form, you write a new
mapping module like this one — extraction and normalization stay
untouched.

Carrier name -> IATA code is a stable, well-known lookup — not
something worth spending LLM calls guessing at. Starter list only;
expand as you encounter more carriers in real documents.
"""

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


def carrier_name_to_code(name: str) -> str:
    if not name:
        return ""
    return CARRIER_NAME_TO_IATA_CODE.get(name.strip().upper(), "")


def split_flight_or_voyage(raw: str) -> tuple[str, str]:
    """
    '2513' alone, or something like 'CZ2513' -> ('CZ', '2513')
    Falls back to ('', raw) if it doesn't look like carrier+number.
    """
    if not raw:
        return "", ""

    import re
    m = re.match(r"^([A-Z]{2})\s*(\d+)$", raw.strip().upper())
    if m:
        return m.group(1), m.group(2)
    return "", raw.strip()


def map_to_ace_fields(normalized: dict) -> dict:
    """
    Returns a dict keyed by the actual ACE form field labels you'd
    target with DOM injection. Values are '' when nothing usable was
    extracted — leave those for manual entry rather than guessing.
    """
    carrier_code = carrier_name_to_code(normalized.get("carrier_name", ""))

    # flight_or_voyage_number in extraction may be just the number
    # ("2513") or combined with a carrier prefix, depending on the
    # source doc's formatting
    _, flight_number = split_flight_or_voyage(normalized.get("flight_or_voyage_number", ""))

    weight_kg = normalized.get("weight_kg")
    gross_weight_whole = str(round(weight_kg)) if weight_kg is not None else ""

    arrival_parsed = normalized.get("estimated_arrival_date_parsed")
    arrival_mmddyy = ""
    if arrival_parsed:
        from datetime import datetime
        dt = datetime.fromisoformat(arrival_parsed)
        arrival_mmddyy = dt.strftime("%m%d%y")

    return {
        "Description of Merchandise": normalized.get("combined_cargo_description", ""),
        "Gross Weight (Kgs)": gross_weight_whole,
        "Carrier Code": carrier_code,
        "Voyage/Flight No": flight_number,
        "Location of Goods": normalized.get("location_of_goods_code", ""),
        "Mode of Transportation": normalized.get("mode_of_transport", ""),
        "Importer of Record": normalized.get("shipper_name", ""),
        "Ultimate Consignee": normalized.get("consignee_name", ""),
        "Estimated Arrival Date": arrival_mmddyy,
        # Fields ACE needs that generally aren't in source documents at
        # all (Entry Type, Bond info, Payment Type, etc.) are left out
        # here on purpose — those come from the importer's own account
        # setup, not from a shipping document, and shouldn't be
        # auto-filled from extraction.
    }