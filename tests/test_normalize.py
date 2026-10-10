"""
Tests for normalize.py — the pure, deterministic cleanup layer (no LLM,
no network). These are cheap to run and exactly the kind of logic that
silently regresses when someone tweaks a regex for one document and
breaks it for another.

Run with:
    pytest test_normalize.py -v
"""

import pytest

from normalize import (
    clean_description,
    extract_weight_kg,
    parse_date_flexible,
    normalize_fields,
)


# ---------------------------------------------------------------------------
# clean_description
# ---------------------------------------------------------------------------

class TestCleanDescription:
    def test_strips_hs_suffix_with_slash(self):
        assert clean_description("Ethernet Switch / HS:8517620020") == "Ethernet Switch"

    def test_strips_hts_suffix_dotted(self):
        assert clean_description("Passive Copper Cable HTS: 8517.62.0020") == "Passive Copper Cable"

    def test_strips_hs_suffix_no_colon(self):
        assert clean_description("Widget HS 8517620020") == "Widget"

    def test_no_suffix_present_unchanged(self):
        assert clean_description("Plain Description") == "Plain Description"

    def test_empty_string(self):
        assert clean_description("") == ""

    def test_none_input(self):
        assert clean_description(None) == ""

    def test_strips_surrounding_whitespace(self):
        assert clean_description("  Padded Text  ") == "Padded Text"

    def test_case_insensitive_hts(self):
        assert clean_description("Item / hs:1234567890") == "Item"


# ---------------------------------------------------------------------------
# extract_weight_kg
# ---------------------------------------------------------------------------

class TestExtractWeightKg:
    def test_kg_explicit(self):
        assert extract_weight_kg("226.000 KG") == 226.0

    def test_kg_lowercase(self):
        assert extract_weight_kg("81.000 kg") == 81.0

    def test_no_unit_defaults_to_kg(self):
        assert extract_weight_kg("150") == 150.0

    def test_lb_converts_to_kg(self):
        # 300 lb -> kg
        result = extract_weight_kg("300 lb")
        assert result == pytest.approx(300 * 0.453592, abs=0.001)

    def test_lbs_plural_converts(self):
        result = extract_weight_kg("100 lbs")
        assert result == pytest.approx(100 * 0.453592, abs=0.001)

    def test_comma_thousands_separator(self):
        assert extract_weight_kg("23,575.500 KG") == 23575.5

    def test_empty_string_returns_none(self):
        assert extract_weight_kg("") is None

    def test_none_input_returns_none(self):
        assert extract_weight_kg(None) is None

    def test_no_number_returns_none(self):
        assert extract_weight_kg("unknown") is None

    def test_rounds_to_three_decimals(self):
        result = extract_weight_kg("100 lb")
        # 100 * 0.453592 = 45.3592 -> rounds to 45.359
        assert result == 45.359


# ---------------------------------------------------------------------------
# parse_date_flexible
# ---------------------------------------------------------------------------

class TestParseDateFlexible:
    def test_dd_mon_yy(self):
        d = parse_date_flexible("11-Jul-26")
        assert d is not None
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_dd_mon_yyyy(self):
        d = parse_date_flexible("11-Jul-2026")
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_mm_dd_yyyy_slash(self):
        d = parse_date_flexible("07/11/2026")
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_iso_format(self):
        d = parse_date_flexible("2026-07-11")
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_month_name_format(self):
        d = parse_date_flexible("July 11, 2026")
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_strips_trailing_time(self):
        d = parse_date_flexible("11-Jul-26 00:00")
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_strips_trailing_time_with_seconds(self):
        d = parse_date_flexible("11-Jul-26 00:00:00")
        assert (d.day, d.month, d.year) == (11, 7, 2026)

    def test_us_dashed_four_digit_year(self):
        # Eagle arrival notices print dates like 09-10-2026
        d = parse_date_flexible("09-10-2026")
        assert (d.day, d.month, d.year) == (10, 9, 2026)

    def test_strips_time_and_timezone(self):
        d = parse_date_flexible("09-28-2026 11:52 (EDT)")
        assert (d.day, d.month, d.year) == (28, 9, 2026)

    def test_uppercase_month_name(self):
        d = parse_date_flexible("06-SEP-2026")  # ISF style
        assert (d.day, d.month, d.year) == (6, 9, 2026)

    def test_legacy_mm_dd_yy_format(self):
        d = parse_date_flexible("02-16-01")
        assert (d.day, d.month, d.year) == (16, 2, 2001)

    def test_empty_string_returns_none(self):
        assert parse_date_flexible("") is None

    def test_none_input_returns_none(self):
        assert parse_date_flexible(None) is None

    def test_unrecognized_format_returns_none(self):
        assert parse_date_flexible("not a date") is None

    def test_whitespace_only_returns_none(self):
        assert parse_date_flexible("   ") is None


# ---------------------------------------------------------------------------
# normalize_fields — the integration-ish one: totals detection, weight
# summing priority, description joining, date fields.
# ---------------------------------------------------------------------------

class TestNormalizeFieldsTotalsDetection:
    def test_explicit_total_weight_wins_over_everything(self):
        fields = {
            "total_weight": "500 KG",
            "cargo_line_items": [
                {"description": "Widget", "weight": "100 KG"},
                {"description": "", "weight": "999 KG"},  # totals-looking row
            ],
        }
        result = normalize_fields(fields)
        assert result["weight_kg"] == 500.0

    def test_detected_totals_row_used_when_no_explicit_total(self):
        fields = {
            "cargo_line_items": [
                {"description": "Widget", "weight": "100 KG"},
                {"description": "", "weight": "999 KG"},  # totals row, no description
            ],
        }
        result = normalize_fields(fields)
        assert result["weight_kg"] == 999.0
        # the totals row itself should NOT show up as a real cargo item
        assert len(result["cargo_line_items"]) == 1
        assert result["cargo_line_items"][0]["description"] == "Widget"

    def test_sums_real_items_when_no_total_available(self):
        fields = {
            "cargo_line_items": [
                {"description": "Widget A", "weight": "100 KG"},
                {"description": "Widget B", "weight": "50 KG"},
            ],
        }
        result = normalize_fields(fields)
        assert result["weight_kg"] == 150.0

    def test_no_weight_data_at_all_returns_none(self):
        fields = {"cargo_line_items": [{"description": "Widget", "weight": ""}]}
        result = normalize_fields(fields)
        assert result["weight_kg"] is None

    def test_empty_cargo_line_items(self):
        fields = {"cargo_line_items": []}
        result = normalize_fields(fields)
        assert result["weight_kg"] is None
        assert result["cargo_line_items"] == []

    def test_missing_cargo_line_items_key(self):
        fields = {}
        result = normalize_fields(fields)
        assert result["weight_kg"] is None
        assert result["cargo_line_items"] == []

    def test_description_only_row_without_weight_not_treated_as_total(self):
        # A row with no description and NO weight either shouldn't set
        # detected_total_kg to something bogus — it just gets skipped.
        fields = {
            "cargo_line_items": [
                {"description": "Widget", "weight": "100 KG"},
                {"description": "", "weight": ""},
            ],
        }
        result = normalize_fields(fields)
        assert result["weight_kg"] == 100.0
        assert len(result["cargo_line_items"]) == 1


class TestNormalizeFieldsDescriptions:
    def test_combines_multiple_descriptions(self):
        fields = {
            "cargo_line_items": [
                {"description": "Widget A", "weight": "10 KG"},
                {"description": "Widget B", "weight": "20 KG"},
            ],
        }
        result = normalize_fields(fields)
        assert result["combined_cargo_description"] == "Widget A; Widget B"

    def test_strips_hs_suffix_in_combined_description(self):
        fields = {
            "cargo_line_items": [
                {"description": "Ethernet Switch / HS:8517620020", "weight": "10 KG"},
            ],
        }
        result = normalize_fields(fields)
        assert result["combined_cargo_description"] == "Ethernet Switch"

    def test_empty_items_produces_empty_description(self):
        fields = {"cargo_line_items": []}
        result = normalize_fields(fields)
        assert result["combined_cargo_description"] == ""


class TestNormalizeFieldsDates:
    def test_document_date_parsed(self):
        fields = {"document_date": "11-Jul-26", "cargo_line_items": []}
        result = normalize_fields(fields)
        assert result["document_date_parsed"] is not None
        assert result["document_date_parsed"].startswith("2026-07-11")

    def test_arrival_date_parsed(self):
        fields = {"estimated_arrival_date": "10-Jul-26", "cargo_line_items": []}
        result = normalize_fields(fields)
        assert result["estimated_arrival_date_parsed"] is not None
        assert result["estimated_arrival_date_parsed"].startswith("2026-07-10")

    def test_missing_dates_are_none(self):
        fields = {"cargo_line_items": []}
        result = normalize_fields(fields)
        assert result["document_date_parsed"] is None
        assert result["estimated_arrival_date_parsed"] is None

    def test_unparseable_date_is_none_not_raises(self):
        fields = {"document_date": "garbage", "cargo_line_items": []}
        result = normalize_fields(fields)
        assert result["document_date_parsed"] is None


class TestNormalizeFieldsPreservesRawData:
    def test_original_fields_not_deleted(self):
        fields = {
            "cargo_line_items": [{"description": "Widget", "weight": "10 KG"}],
            "some_other_field": "untouched",
        }
        result = normalize_fields(fields)
        assert result["some_other_field"] == "untouched"

    def test_does_not_mutate_input_dict(self):
        original = {
            "cargo_line_items": [{"description": "Widget", "weight": "10 KG"}],
        }
        import copy
        snapshot = copy.deepcopy(original)
        normalize_fields(original)
        # normalize_fields should operate on a copy, not the caller's dict
        assert original == snapshot


if __name__ == "__main__":
    pytest.main([__file__, "-v"])