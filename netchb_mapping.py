"""
Maps normalized, general-purpose extraction output to real NetCHB form
field IDs, ready for a content script to inject via
document.getElementById(id).value = ...

This is the NetCHB-specific counterpart to ace_mapping.py. Extraction
and normalization stay generic; this file is where NetCHB's actual DOM
structure and quirks live.
"""

# --- Mode of Transportation: NetCHB's actual dropdown options ---
# Our extraction schema currently produces loose text like "Air" or
# "Surface" for mode_of_transport. "Surface" is NOT specific enough —
# NetCHB has no generic surface option, only Truck/Rail/Vessel/etc
# broken out separately. This mapping is necessarily a best guess for
# anything other than "Air"; see note in map_mode_of_transport().
MODE_TRANSPORT_CODES = {
    "vessel": "10",
    "ocean": "10",
    "ship": "10",
    "rail": "20",
    "truck": "30",
    "road": "30",
    "surface": "30",  # ambiguous default — see note below
    "auto": "32",
    "air": "40",
    "mail": "50",
}


# Modes where a Master B/L and Master SCAC apply (ocean-type moves).
# Adjust this set if you find NetCHB shows these fields for other
# modes too — worth confirming directly in the browser.
MODES_WITH_MASTER_BL = {"10", "11", "12"}

# Carrier code lookups are mode-specific: air uses IATA codes, ocean
# uses SCAC codes. Only air is populated for now — extend
# CARRIER_NAME_TO_SCAC as you encounter real ocean carriers in testing.
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

CARRIER_NAME_TO_SCAC = {
    "CMA CGM": "CMDU",
    # e.g. "MAERSK": "MAEU", "COSCO": "COSU" — fill in as you test
    # real ocean BOLs. Left empty on purpose rather than guessing.
}



def map_mode_of_transport(mode_text: str) -> str:
    """
    'Air' -> '40', 'Surface' -> '30' (best-effort guess — flagged),
    unrecognized -> ''
    """
    if not mode_text:
        return ""
    key = mode_text.strip().lower()
    code = MODE_TRANSPORT_CODES.get(key, "")
    return code


def map_carrier_code(carrier_name: str, mode_code: str) -> str:
    if not carrier_name:
        return ""
    name = carrier_name.strip().upper()
    if mode_code in ("40", "41"):  # air
        return CARRIER_NAME_TO_IATA_CODE.get(name, "")
    if mode_code in MODES_WITH_MASTER_BL or mode_code in ("20", "21", "30", "31"):
        # ocean, rail, and truck all use SCAC codes (issued by NMFTA)
        return CARRIER_NAME_TO_SCAC.get(name, "")
    return ""


def split_flight_or_voyage(raw: str) -> tuple:
    """
    '2513' alone, or 'CZ2513' -> just the number part '2513'.
    Falls back to the raw value if it doesn't look like carrier+number.
    """
    if not raw:
        return "", ""
    import re
    m = re.match(r"^([A-Z]{2})\s*(\d+)$", raw.strip().upper())
    if m:
        return m.group(1), m.group(2)
    return "", raw.strip()


def map_to_netchb_fields(normalized: dict) -> dict:
    """
    Returns {field_id: value} pairs matching NetCHB's real DOM ids,
    ready for direct injection: document.getElementById(id).value = v
    """
    mode_code = map_mode_of_transport(normalized.get("mode_of_transport", ""))
    carrier_code = map_carrier_code(normalized.get("carrier_name", ""), mode_code)
    _, flight_number = split_flight_or_voyage(normalized.get("flight_or_voyage_number", ""))

    weight_kg = normalized.get("weight_kg")
    gross_weight_whole = str(round(weight_kg)) if weight_kg is not None else ""

    def mmddyy(iso_date_str):
        if not iso_date_str:
            return ""
        from datetime import datetime
        return datetime.fromisoformat(iso_date_str).strftime("%m%d%y")

    fields = {
        "imp": normalized.get("shipper_name", ""),           # Importer of Record
        "ulc": normalized.get("consignee_name", ""),         # Ultimate Consignee
        "mot": mode_code,                                     # Mode of Transportation
        "grw": gross_weight_whole,                            # Gross Weight
        "desc": normalized.get("combined_cargo_description", ""),  # Description
        "locGoods": normalized.get("location_of_goods_code", ""),  # Location of Goods
        "cCode": carrier_code,                                # Carrier Code
        "vNo": flight_number,                                 # Voyage/Flight No
        "dImp": mmddyy(normalized.get("document_date_parsed")),
        "eArrive": mmddyy(normalized.get("estimated_arrival_date_parsed")),
        # "pEntry" (Port of Entry) intentionally omitted — this isn't
        # something that comes from a shipping document; it's tied to
        # the broker's own filing setup.
    }

    # Master SCAC / Master B/L only apply for ocean-type modes
    if mode_code in MODES_WITH_MASTER_BL:
        fields["scac0"] = map_carrier_code(normalized.get("carrier_name", ""), mode_code)
        fields["mb0"] = normalized.get("master_bol_number", "")

    return fields