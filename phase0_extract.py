"""
Phase 0: Validate the core pipeline before building any app around it.

Two extraction modes:
  --mode rules  (default) : regex-based, no API key, no cost, instant.
                             Works well for ONE fixed document template.
  --mode llm               : Gemini-based, generalizes across different
                              BOL layouts, but needs GEMINI_API_KEY and
                              a working quota.

Usage:
    python phase0_extract.py path/to/your_bol.pdf
    python phase0_extract.py path/to/your_bol.pdf --mode llm
"""

import sys
import re
import json
import os
import argparse

from dotenv import load_dotenv
load_dotenv()  # loaded once, up front, so env checks later in the script work

from normalize import normalize_fields
from ace_mapping import map_to_ace_fields
from netchb_mapping import map_to_netchb_fields


def extract_markdown(file_path: str) -> str:
    """Step A: Docling converts the raw document into structural markdown."""
    from docling.document_converter import DocumentConverter

    print(f"⏳ Running Docling on: {file_path}")
    converter = DocumentConverter()
    result = converter.convert(file_path)
    return result.document.export_to_markdown()


# ---------------------------------------------------------------------------
# MODE: rules — fast, free, but tailored to THIS document's exact template.
# A differently-laid-out BOL will likely need different patterns below.
# That fragility is the actual tradeoff vs. the LLM mode.
# ---------------------------------------------------------------------------

def _first(pattern, text, group=1, flags=re.IGNORECASE):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else ""


def parse_header_fields_rules(markdown: str) -> dict:
    fields = {}
    fields["bol_date"] = _first(r"Date:\s*([^\n]+)", markdown)
    fields["carrier_name"] = _first(r"Carrier:\s*([^\n]+)", markdown)
    fields["bill_to_number"] = _first(r"Bill to:\s*([^\n]+)", markdown)

    # First occurrence only — this doc repeats the whole form twice
    fields["shipper_name"] = _first(r"Shipper:\s*\n+\s*([^\n]+)", markdown)
    fields["consignee_name"] = _first(r"Consignee:\s*([^\n]+)", markdown)

    street_matches = re.findall(r"Street:\s*\n*\s*([^\n]*)", markdown)
    city_matches = re.findall(r"City/State:?\s*\n*\s*([^\n]*)", markdown)

    fields["shipper_street"] = street_matches[0].strip() if len(street_matches) > 0 else ""
    fields["shipper_city_state"] = city_matches[1].strip() if len(city_matches) > 1 else ""

    consignee_line = street_matches[1] if len(street_matches) > 1 else ""
    cm = re.match(r"(.*?)\s*City/State:\s*(.*)", consignee_line)
    if cm:
        fields["consignee_street"] = cm.group(1).strip()
        fields["consignee_city_state"] = cm.group(2).strip()
    else:
        fields["consignee_street"] = consignee_line.strip()
        fields["consignee_city_state"] = ""

    return fields


def parse_cargo_table_rules(markdown: str) -> list:
    """
    Docling flattened this table into sequential lines (no markdown pipes).
    Reconstruct rows using simple type rules:
      - two ints in a row -> pieces, pallets
      - then: another int right away -> TOTALS row (weight)
              text -> description, optional ref code, then weight int
    """
    m = re.search(
        r"RSF WPO\s*\n+\s*Weight\s*\n+(.*?)Shippers Signature:",
        markdown,
        re.DOTALL,
    )
    if not m:
        return []

    lines = [l.strip() for l in m.group(1).split("\n") if l.strip()]

    rows = []
    i = 0
    while i < len(lines):
        if not lines[i].isdigit():
            i += 1
            continue
        pieces = lines[i]
        i += 1
        if i >= len(lines) or not lines[i].isdigit():
            break
        pallets = lines[i]
        i += 1

        if i < len(lines) and lines[i].isdigit():
            rows.append({
                "pieces": pieces, "pallets": pallets,
                "description": "TOTAL", "reference_code": "",
                "weight": lines[i],
            })
            i += 1
            continue

        description = lines[i] if i < len(lines) else ""
        i += 1

        reference_code = ""
        if i < len(lines) and re.match(r"^\d+-\d+$", lines[i]):
            reference_code = lines[i]
            i += 1

        weight = lines[i] if i < len(lines) and lines[i].isdigit() else ""
        i += 1

        rows.append({
            "pieces": pieces, "pallets": pallets,
            "description": description, "reference_code": reference_code,
            "weight": weight,
        })

    return rows


def extract_with_rules(markdown: str) -> dict:
    fields = parse_header_fields_rules(markdown)
    fields["cargo"] = parse_cargo_table_rules(markdown)
    return fields


# ---------------------------------------------------------------------------
# MODE: llm — generalizes across layouts, needs GEMINI_API_KEY
# ---------------------------------------------------------------------------

BOL_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "description": "e.g. 'Bill of Lading', 'Air Freight Arrival Notice', 'Air Waybill'",
        },
        "mode_of_transport": {
            "type": "string",
            "description": "e.g. 'Air', 'Ocean', 'Truck' — infer from document content",
        },
        # Parties
        "shipper_name": {"type": "string"},
        "shipper_address": {"type": "string"},
        "consignee_name": {"type": "string"},
        "consignee_address": {"type": "string"},
        "notify_party_name": {"type": "string"},
        # Identifiers — different transport modes use different ones;
        # fill in whichever actually appear, leave the rest empty.
        #
        # Ocean shipments can carry TWO distinct B/L numbers: the
        # Master/Ocean B/L (issued by the carrier) and the House B/L
        # (issued by the forwarder). These are different numbers, not
        # two labels for the same thing — NetCHB's Master B/L field
        # specifically wants master_bol_number, so conflating the two
        # would put the wrong number on the form.
        "master_bol_number": {
            "type": "string",
            "description": "Master/Ocean Bill of Lading number — the carrier's own B/L, e.g. an 'Ocean Bill of Lading' field",
        },
        "house_bol_number": {
            "type": "string",
            "description": "House Bill of Lading number — issued by the forwarder, distinct from the master/ocean B/L",
        },
        "mawb_number": {"type": "string"},
        "hawb_number": {"type": "string"},
        "shipment_reference": {"type": "string"},
        # Carrier / routing
        "carrier_name": {"type": "string"},
        "flight_or_voyage_number": {"type": "string"},
        "port_or_airport_of_loading": {"type": "string"},
        "port_or_airport_of_discharge": {"type": "string"},
        "location_of_goods_code": {
            "type": "string",
            "description": "FIRMS code or similar facility/location code, if present",
        },
        # Cargo — a document can have multiple line items (different
        # goods, different weights/quantities per row). Extract each
        # row separately rather than squashing them into one string.
        "cargo_line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "pieces": {"type": "string"},
                    "pallets_or_units": {"type": "string"},
                    "weight": {"type": "string", "description": "raw value + unit as written"},
                    "harmonized_code": {"type": "string"},
                },
            },
        },
        # If the document states an explicit grand total weight
        # (common — e.g. a "TOTAL" row), capture it here as raw text.
        # More reliable than summing line items ourselves when it's
        # directly stated. Leave empty if there's no explicit total.
        "total_weight": {"type": "string", "description": "raw value + unit as written"},
        # Dates
        "document_date": {"type": "string", "description": "raw date as written in the document"},
        "estimated_arrival_date": {"type": "string", "description": "raw date as written"},
    },
    "required": [
        "document_type",
        "shipper_name", "shipper_address",
        "consignee_name", "consignee_address", "cargo_line_items",
    ],
}


# Shared instruction block for the Master/House B/L distinction — kept as
# one string so Gemini and Groq never drift out of sync with each other.
MASTER_HOUSE_BOL_INSTRUCTION = (
    "For ocean shipments, capture master_bol_number (the carrier's "
    "Ocean Bill of Lading) and house_bol_number (the forwarder's House "
    "Bill of Lading) separately if both appear — they are different "
    "numbers, not the same number under different names. "
)


def extract_with_gemini(markdown: str) -> dict:
    from google import genai

    client = genai.Client()  # reads GEMINI_API_KEY from env
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=markdown,
        config={
            "system_instruction": (
                "You extract structured data from logistics/shipping "
                "documents (Bills of Lading, air waybills, arrival "
                "notices, etc). Identify document_type and "
                "mode_of_transport. Only fill in identifier fields "
                "(master_bol_number, house_bol_number, mawb_number, "
                "hawb_number, shipment_reference) that actually appear "
                "— leave others empty. "
                "IMPORTANT: cargo tables often have multiple rows — one "
                "per distinct good/shipment line. Extract EACH row as a "
                "separate entry in cargo_line_items; do not merge rows "
                "or drop any. If the table has a totals/grand-total row, "
                "put that value in total_weight, not in "
                "cargo_line_items. "
                "Keep weight and dates as raw text exactly as written; "
                "do not convert units or reformat dates yourself. Only "
                "use information present in the text; never guess or "
                "infer a value that isn't explicitly there. "
                + MASTER_HOUSE_BOL_INSTRUCTION
            ),
            "response_mime_type": "application/json",
            "response_schema": BOL_SCHEMA,
        },
    )
    return json.loads(response.text)


def extract_with_groq(markdown: str) -> dict:
    from groq import Groq

    client = Groq()  # reads GROQ_API_KEY from env

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract structured data from logistics/shipping "
                    "documents (Bills of Lading, air waybills, arrival "
                    "notices, etc). Identify document_type and "
                    "mode_of_transport. Only fill in identifier fields "
                    "(master_bol_number, house_bol_number, mawb_number, "
                    "hawb_number, shipment_reference) that actually "
                    "appear — leave others empty. "
                    "IMPORTANT: cargo tables often have multiple rows — "
                    "one per distinct good/shipment line. Extract EACH "
                    "row as a separate entry in cargo_line_items; do not "
                    "merge multiple rows into one entry or drop rows. If "
                    "the table has a totals/grand-total row, put that "
                    "value in total_weight, not in cargo_line_items. "
                    "Keep weight and dates as raw text exactly as written "
                    "in the document; do not convert units or reformat "
                    "dates yourself. "
                    "Only use information present in the text; never guess "
                    "or infer a value that isn't explicitly there. "
                    + MASTER_HOUSE_BOL_INSTRUCTION +
                    "Respond with ONLY valid JSON matching this schema, no "
                    "other text:\n" + json.dumps(BOL_SCHEMA)
                ),
            },
            {"role": "user", "content": markdown},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 0: extract structured fields from a BOL document."
    )
    parser.add_argument("bol_path", help="Path to the BOL file (pdf/png/jpg)")
    parser.add_argument(
        "--mode",
        choices=["rules", "gemini", "groq"],
        default="rules",
        help="Extraction mode: 'rules' (free, template-specific), "
             "'gemini' (needs GEMINI_API_KEY), or "
             "'groq' (needs GROQ_API_KEY). Default: rules",
    )
    parser.add_argument(
        "--target",
        choices=["ace", "netchb", "both"],
        default="both",
        help="Which destination mapping to print (only applies to "
             "gemini/groq modes). Default: both",
    )
    args = parser.parse_args()

    try:
        markdown = extract_markdown(args.bol_path)
    except Exception as e:
        print(f"❌ Docling failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("RAW MARKDOWN (this is what Docling saw)")
    print("=" * 60)
    print(markdown)

    print("\n" + "=" * 60)
    print(f"STRUCTURED JSON (mode: {args.mode})")
    print("=" * 60)

    if args.mode == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            print("⚠️  GEMINI_API_KEY not set — check your .env file.")
            sys.exit(1)
        try:
            fields = extract_with_gemini(markdown)
        except Exception as e:
            print(f"❌ Gemini structuring failed: {e}")
            sys.exit(1)
    elif args.mode == "groq":
        if not os.environ.get("GROQ_API_KEY"):
            print("⚠️  GROQ_API_KEY not set — check your .env file.")
            sys.exit(1)
        try:
            fields = extract_with_groq(markdown)
        except Exception as e:
            print(f"❌ Groq structuring failed: {e}")
            sys.exit(1)
    else:
        fields = extract_with_rules(markdown)

    print(json.dumps(fields, indent=2))

    # Rules mode uses a different, ad-hoc field structure (not the
    # general BOL_SCHEMA), so mapping only makes sense for the LLM
    # modes right now.
    if args.mode in ("gemini", "groq"):
        normalized = normalize_fields(fields)

        if args.target in ("ace", "both"):
            ace_ready = map_to_ace_fields(normalized)
            print("\n" + "=" * 60)
            print("MAPPED TO ACE FORM FIELDS")
            print("=" * 60)
            print(json.dumps(ace_ready, indent=2))

        if args.target in ("netchb", "both"):
            netchb_ready = map_to_netchb_fields(normalized)
            print("\n" + "=" * 60)
            print("MAPPED TO NETCHB FORM FIELDS (id: value)")
            print("=" * 60)
            print(json.dumps(netchb_ready, indent=2))


if __name__ == "__main__":
    main()