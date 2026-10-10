"""
Deterministic identifier rules — no LLM, no network.

These encode rules the client confirmed, plus lessons from real sample
documents, so the code can validate or override what the LLM extracted:

  * An OCEAN master B/L number starts with 4 letters (that prefix is the
    carrier's SCAC, e.g. EGLV, CMDU).
  * An AIR master waybill (MAWB) number starts with 3 digits.
  * The client uses three modes: 10 Vessel non-container (loose freight),
    11 Vessel container (whole container), 40 Air non-container.

Why labels can't be trusted: Eagle's arrival notice prints "MASTER B/L NO.
ELS910282" — that's the forwarder's own file number (3 letters), not the
carrier's master. The real master (EGLV010600860793) appears under
"AMS B/L NO." on one version and "MBL NO." on the BOL and ISF. Choosing the
candidate that matches the rule is more reliable than trusting the label.

Caveat: a HOUSE B/L can also start with 4 letters (e.g. MGNGHST26718481), so
the 4-letter rule alone can't tell master from house. pick_master_number()
therefore takes the known house number and excludes it.
"""

import re

# ---------------------------------------------------------------------------
# Master number classification
# ---------------------------------------------------------------------------

_OCEAN_MASTER = re.compile(r"^[A-Z]{4}")
_AIR_MASTER = re.compile(r"^\d{3}")


def normalize_number(raw) -> str:
    """Uppercase and strip spaces/hyphens: '112-0933 1195' -> '11209331195'."""
    if not raw or not isinstance(raw, str):
        return ""
    return re.sub(r"[\s\-]", "", raw).upper()


def classify_master_number(raw) -> str | None:
    """'ocean' (starts with 4 letters), 'air' (starts with 3 digits), or None."""
    n = normalize_number(raw)
    if _OCEAN_MASTER.match(n):
        return "ocean"
    if _AIR_MASTER.match(n):
        return "air"
    return None


def pick_master_number(candidates, house=None) -> str | None:
    """
    First candidate that looks like an ocean master and is not the house
    number. Pass candidates in order of trust (e.g. the field labelled
    master first). Returns the normalized number, or None — an empty field
    beats a wrong one on a customs entry.
    """
    house_n = normalize_number(house)
    for c in candidates or []:
        n = normalize_number(c)
        if n and n != house_n and classify_master_number(n) == "ocean":
            return n
    return None


def scac_from_master(raw) -> str:
    """Master SCAC = first 4 letters of an ocean master. '' for anything else."""
    n = normalize_number(raw)
    return n[:4] if classify_master_number(n) == "ocean" else ""


# ---------------------------------------------------------------------------
# Transport mode
# ---------------------------------------------------------------------------

_AIR_WORDS = ("air",)
_OCEAN_WORDS = ("vessel", "ocean", "sea", "ship")


def _family_from_text(mode_text) -> str | None:
    t = (mode_text or "").lower()
    if any(w in t for w in _AIR_WORDS):
        return "air"
    if any(w in t for w in _OCEAN_WORDS):
        return "ocean"
    return None


def transport_family(master_number, mode_text="") -> str | None:
    """'air' / 'ocean' / None. The master-number rule outranks the LLM's text."""
    return classify_master_number(master_number) or _family_from_text(mode_text)


def check_mode_consistency(mode_text, master_number) -> tuple[bool, str]:
    """
    Cross-check the LLM's mode_of_transport against the master number.
    Returns (ok, reason). Missing information is not a conflict.
    """
    by_number = classify_master_number(master_number)
    by_text = _family_from_text(mode_text)
    if by_number is None or by_text is None:
        return True, "not enough information to cross-check"
    if by_number != by_text:
        return False, (
            f"mode says {by_text} but master number "
            f"'{normalize_number(master_number)}' looks {by_number}"
        )
    return True, "ok"


def parse_service_type(raw) -> str | None:
    """
    'FCL' (whole container) or 'LCL' (loose/consolidated), or None.
    FCL/CY/CY -> FCL;  LCL/CFS/CFS -> LCL;  mixed like CY/CFS -> None.
    """
    tokens = set(re.findall(r"[A-Z]+", (raw or "").upper()))
    if "FCL" in tokens:
        return "FCL"
    if "LCL" in tokens:
        return "LCL"
    has_cy, has_cfs = "CY" in tokens, "CFS" in tokens
    if has_cy and not has_cfs:
        return "FCL"
    if has_cfs and not has_cy:
        return "LCL"
    return None


# UNCONFIRMED for LCL: the client defines 10 as "loose freight" and 11 as
# "whole container". A CFS/CFS (LCL) shipment travels inside a container
# but isn't a whole-container move, so 10 is the likely answer — but ask the
# client. This table is the one place to change if the answer is 11.
SERVICE_TYPE_TO_OCEAN_MODE = {
    "FCL": "11",
    "LCL": "10",
}


def ocean_mode_code(service_type_raw) -> str | None:
    """'11' for FCL, '10' for LCL, None when unknown (don't guess)."""
    return SERVICE_TYPE_TO_OCEAN_MODE.get(parse_service_type(service_type_raw))


# ---------------------------------------------------------------------------
# Vessel / voyage
# ---------------------------------------------------------------------------

def split_vessel_and_voyage(raw) -> tuple[str, str]:
    """
    'CMA CGM PEGASUS 0P52KE1MA'   -> ('CMA CGM PEGASUS', '0P52KE1MA')
    'COSCO DEVELOPMENT / 081E'    -> ('COSCO DEVELOPMENT', '081E')
    With no '/', the last token is the voyage only if it contains a digit;
    otherwise the whole string is treated as the vessel name.
    """
    if not raw or not isinstance(raw, str):
        return "", ""
    s = raw.strip()
    if "/" in s:
        vessel, _, voyage = s.rpartition("/")
        return vessel.strip(), voyage.strip()
    parts = s.rsplit(None, 1)
    if len(parts) == 2 and re.search(r"\d", parts[1]):
        return parts[0].strip(), parts[1].strip()
    return s, ""