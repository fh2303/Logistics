"""
Tests for netchb_mapping.py, built from the real sample shipments:
  * Eagle  — ocean LCL (CFS/CFS), two arrival-notice versions
  * E-LINK — ocean FCL, six 40HC containers
  * Alibaba — air, China Eastern MU589
Run:  python -m pytest test_netchb_mapping.py -v
"""

import json

import pytest

from netchb_mapping import map_to_netchb_fields, netchb_warnings

EAGLE = {  # the 09-28 notice: master sits in the "AMS B/L NO." field
    "mode_of_transport": "Ocean",
    "shipper_name": "JINSHENG TRADE (GUANGZHOU) CO.,LTD",
    "consignee_name": "WONTON FOOD CORPORATION(TX)",
    "master_bol_number": "ELS910282",          # forwarder file no., NOT the master
    "ams_bol_number": "EGLV010600860793",
    "house_bol_number": "MGNGHST26718481",
    "service_type": "CFS/CFS",
    "flight_or_voyage_number": "CMA CGM PEGASUS 0P52KE1MA",
    "carrier_name": "CMA CGM",
    "location_of_goods_code": "V209",
    "combined_cargo_description": "FIBERGLASS ROD",
    "weight_kg": 943.0,
    "estimated_arrival_date_parsed": "2026-09-10T00:00:00",
    "document_date_parsed": "2026-09-28T00:00:00",
}

ELINK = {
    "mode_of_transport": "Ocean",
    "shipper_name": "E-LINK TECHNOLOGIES LIMITED",
    "consignee_name": "E-LINK LLC",
    "master_bol_number": "CMDUXIA1864191",
    "house_bol_number": "CFZMF260500447",
    "service_type": "40HC FCL",
    "flight_or_voyage_number": "COSCO DEVELOPMENT / 081E",
    "weight_kg": 23575.5,
    "estimated_arrival_date_parsed": "2026-07-10T00:00:00",
}

ALIBABA = {
    "mode_of_transport": "Air",
    "shipper_name": "ZHEJIANG ZHAOLONG INTERCONNECT TECHNOLOGY CO LTD",
    "mawb_number": "11209331195",
    "hawb_number": "CTSNJK0819",
    "carrier_name": "CHINA EASTERN AIRLINES",
    "flight_or_voyage_number": "MU589",
    "weight_kg": 81.0,
    "estimated_arrival_date_parsed": "2026-08-03T00:00:00",
}


class TestOceanLCL:
    def test_eagle_fields(self):
        f = map_to_netchb_fields(EAGLE)
        assert f["mot"] == "10"                    # CFS/CFS -> loose freight (unconfirmed)
        assert f["mb0"] == "EGLV010600860793"      # not ELS910282, not the house number
        assert f["scac0"] == "EGLV"                # prefix, not CMA CGM's CMDU
        assert f["vNo"] == "0P52KE1MA"             # voyage only
        assert f["grw"] == "943"
        assert f["eArrive"] == "091026"
        assert f["locGoods"] == "V209"
        assert f["cCode"] == ""                    # ocean carrier code: open client question

    def test_eagle_no_warnings(self):
        assert netchb_warnings(EAGLE) == []

    def test_older_notice_has_house_in_ams_field(self):
        # the 09-17 version: AMS field holds the house number, so there is no
        # usable master in this document. Blank beats wrong.
        old = {**EAGLE, "ams_bol_number": "MGNGHST26718481"}
        f = map_to_netchb_fields(old)
        assert f["mb0"] == "" and f["scac0"] == ""
        assert any("No valid ocean master" in w for w in netchb_warnings(old))

    def test_master_from_other_document_fills_the_gap(self):
        # once the BOL's "MBL NO." is merged in, the master is recovered
        merged = {**EAGLE, "ams_bol_number": "MGNGHST26718481",
                  "master_bol_number": "EGLV010600860793"}
        assert map_to_netchb_fields(merged)["mb0"] == "EGLV010600860793"


class TestOceanFCL:
    def test_elink_fields(self):
        f = map_to_netchb_fields(ELINK)
        assert f["mot"] == "11"
        assert f["scac0"] == "CMDU"
        assert f["mb0"] == "CMDUXIA1864191"
        assert f["vNo"] == "081E"

    def test_unknown_load_type_leaves_mot_blank_but_keeps_master(self):
        f = map_to_netchb_fields({**ELINK, "service_type": ""})
        assert f["mot"] == ""
        assert f["mb0"] == "CMDUXIA1864191" and f["scac0"] == "CMDU"
        assert any("FCL vs LCL" in w for w in netchb_warnings({**ELINK, "service_type": ""}))


class TestAir:
    def test_alibaba_fields(self):
        f = map_to_netchb_fields(ALIBABA)
        assert f["mot"] == "40"
        assert f["cCode"] == "MU"
        assert f["vNo"] == "589"
        assert f["grw"] == "81"
        assert f["eArrive"] == "080326"

    def test_air_has_no_master_bl_fields(self):
        f = map_to_netchb_fields(ALIBABA)
        assert "scac0" not in f and "mb0" not in f

    def test_air_field_set(self):
        assert set(map_to_netchb_fields(ALIBABA)) == {
            "imp", "ulc", "mot", "grw", "desc", "locGoods",
            "cCode", "vNo", "dImp", "eArrive"}

    def test_air_no_warnings(self):
        assert netchb_warnings(ALIBABA) == []


class TestConflictsAndGaps:
    def test_number_outranks_llm_mode_text(self):
        wrong = {**ELINK, "mode_of_transport": "Air"}
        assert map_to_netchb_fields(wrong)["mot"] == "11"
        assert any("Mode conflict" in w for w in netchb_warnings(wrong))

    def test_nothing_to_decide_with(self):
        f = map_to_netchb_fields({"mode_of_transport": "Surface"})
        assert f["mot"] == ""
        assert any("air from ocean" in w for w in netchb_warnings({"mode_of_transport": "Surface"}))

    def test_text_fallback_when_no_numbers(self):
        f = map_to_netchb_fields({"mode_of_transport": "Air"})
        assert f["mot"] == "40"

    def test_missing_weight_and_dates_are_blank(self):
        f = map_to_netchb_fields({"mode_of_transport": "Air"})
        assert f["grw"] == "" and f["eArrive"] == "" and f["dImp"] == ""

    def test_output_is_json_serializable_strings(self):
        for sample in (EAGLE, ELINK, ALIBABA):
            f = map_to_netchb_fields(sample)
            json.dumps(f)
            assert all(isinstance(v, str) for v in f.values())