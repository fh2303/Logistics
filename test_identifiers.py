"""
Tests for identifiers.py, using real numbers from the sample documents.
Run:  python -m pytest test_identifiers.py -v
"""

import pytest

from identifiers import (
    check_mode_consistency,
    classify_master_number,
    normalize_number,
    ocean_mode_code,
    parse_service_type,
    pick_master_number,
    scac_from_master,
    split_vessel_and_voyage,
    transport_family,
)

EAGLE_MASTER = "EGLV010600860793"
EAGLE_HOUSE = "MGNGHST26718481"
EAGLE_FILE_NO = "ELS910282"  # printed as "MASTER B/L NO." but it's the forwarder's file no.
ELINK_OCEAN_BL = "CMDUXIA1864191"
ALIBABA_MAWB = "11209331195"


class TestNormalize:
    def test_strips_spaces_hyphens_and_uppercases(self):
        assert normalize_number(" 112-0933 1195 ") == "11209331195"
        assert normalize_number("eglv010600860793") == EAGLE_MASTER

    @pytest.mark.parametrize("bad", [None, "", "   ", 123])
    def test_bad_input_is_empty(self, bad):
        assert normalize_number(bad) == ""


class TestClassify:
    def test_ocean_masters(self):
        assert classify_master_number(EAGLE_MASTER) == "ocean"
        assert classify_master_number(ELINK_OCEAN_BL) == "ocean"

    def test_air_master(self):
        assert classify_master_number(ALIBABA_MAWB) == "air"
        assert classify_master_number("112-09331195") == "air"

    def test_forwarder_file_number_is_neither(self):
        # 3 letters then digits: fails the "4 letters" rule
        assert classify_master_number(EAGLE_FILE_NO) is None

    def test_lowercase_ocean(self):
        assert classify_master_number("eglv010600860793") == "ocean"

    @pytest.mark.parametrize("bad", [None, "", "12", "AB1234"])
    def test_unclassifiable(self, bad):
        assert classify_master_number(bad) is None

    def test_house_number_also_matches_ocean_rule(self):
        # documents the caveat: the rule alone can't separate master from house
        assert classify_master_number(EAGLE_HOUSE) == "ocean"


class TestPickMaster:
    def test_skips_forwarder_file_number(self):
        assert pick_master_number([EAGLE_FILE_NO, EAGLE_MASTER]) == EAGLE_MASTER

    def test_excludes_house_even_though_it_matches_rule(self):
        # the 09-17 arrival notice: AMS B/L field holds the house number
        got = pick_master_number([EAGLE_FILE_NO, EAGLE_HOUSE], house=EAGLE_HOUSE)
        assert got is None

    def test_house_exclusion_is_format_insensitive(self):
        got = pick_master_number([EAGLE_HOUSE.lower()], house=" " + EAGLE_HOUSE)
        assert got is None

    def test_prefers_earlier_candidate(self):
        assert pick_master_number([ELINK_OCEAN_BL, EAGLE_MASTER]) == ELINK_OCEAN_BL

    def test_returns_normalized_value(self):
        assert pick_master_number(["eglv 0106 00860793"]) == EAGLE_MASTER

    @pytest.mark.parametrize("cands", [None, [], [None, ""], [ALIBABA_MAWB]])
    def test_none_when_nothing_qualifies(self, cands):
        assert pick_master_number(cands) is None


class TestScac:
    def test_from_ocean_master(self):
        assert scac_from_master(EAGLE_MASTER) == "EGLV"
        assert scac_from_master(ELINK_OCEAN_BL) == "CMDU"

    @pytest.mark.parametrize("raw", [ALIBABA_MAWB, EAGLE_FILE_NO, "", None])
    def test_empty_when_not_ocean(self, raw):
        assert scac_from_master(raw) == ""


class TestModeConsistency:
    def test_agreement(self):
        assert check_mode_consistency("Ocean", EAGLE_MASTER)[0]
        assert check_mode_consistency("Air", ALIBABA_MAWB)[0]
        assert check_mode_consistency("Vessel", EAGLE_MASTER)[0]

    def test_air_text_with_ocean_number_conflicts(self):
        ok, reason = check_mode_consistency("Air", EAGLE_MASTER)
        assert not ok and "ocean" in reason

    def test_ocean_text_with_air_number_conflicts(self):
        assert not check_mode_consistency("Ocean", ALIBABA_MAWB)[0]

    def test_missing_info_is_not_a_conflict(self):
        assert check_mode_consistency("", EAGLE_MASTER)[0]
        assert check_mode_consistency("Air", "")[0]
        assert check_mode_consistency("Truck", EAGLE_MASTER)[0]


class TestTransportFamily:
    def test_number_outranks_llm_text(self):
        assert transport_family(EAGLE_MASTER, "Air") == "ocean"

    def test_falls_back_to_text(self):
        assert transport_family("", "Air") == "air"
        assert transport_family(None, "Sea freight") == "ocean"

    def test_unknown(self):
        assert transport_family("", "") is None


class TestServiceType:
    @pytest.mark.parametrize("raw,expected", [
        ("FCL", "FCL"),
        ("40HC FCL", "FCL"),
        ("CY/CY", "FCL"),
        ("LCL", "LCL"),
        ("CFS/CFS", "LCL"),
        ("cfs-cfs", "LCL"),
        ("CY/CFS", None),   # mixed: ambiguous, don't guess
        ("", None),
        (None, None),
        ("EXPRESS RELEASE", None),
    ])
    def test_parse(self, raw, expected):
        assert parse_service_type(raw) == expected

    def test_mode_codes(self):
        assert ocean_mode_code("FCL") == "11"
        assert ocean_mode_code("CFS/CFS") == "10"   # UNCONFIRMED with client
        assert ocean_mode_code("CY/CFS") is None
        assert ocean_mode_code(None) is None


class TestVesselVoyage:
    @pytest.mark.parametrize("raw,expected", [
        ("CMA CGM PEGASUS 0P52KE1MA", ("CMA CGM PEGASUS", "0P52KE1MA")),
        ("CMA CGM PEGASUS / 0P52KE1MA", ("CMA CGM PEGASUS", "0P52KE1MA")),
        ("COSCO DEVELOPMENT / 081E", ("COSCO DEVELOPMENT", "081E")),
        ("MSC OSCAR", ("MSC OSCAR", "")),
        ("", ("", "")),
        (None, ("", "")),
    ])
    def test_split(self, raw, expected):
        assert split_vessel_and_voyage(raw) == expected