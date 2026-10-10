"""
Tests for merge.py, using the real Eagle shipment: two versions of the
arrival notice (09-17 and 09-28), the BOL, invoice, packing list and ISF.
Run:  python -m pytest test_merge.py -v
"""

from merge import classify_document, merge_shipment_fields
from netchb_mapping import map_to_netchb_fields
from normalize import normalize_fields

MASTER = "EGLV010600860793"
HOUSE = "MGNGHST26718481"

NOTICE_NEW = {  # prepared 09-28: master sits in the AMS field
    "document_type": "Arrival Notice / Freight Invoice",
    "prepared_date": "09-28-2026 11:52 (EDT)",
    "mode_of_transport": "Ocean",
    "shipper_name": "JINSHENG TRADE (GUANGZHOU) CO.,LTD",
    "consignee_name": "WONTON FOOD CORPORATION(TX)",
    "master_bol_number": "ELS910282",            # forwarder file no., not the master
    "ams_bol_number": MASTER,
    "house_bol_number": HOUSE,
    "service_type": "CFS/CFS",
    "flight_or_voyage_number": "CMA CGM PEGASUS 0P52KE1MA",
    "location_of_goods_code": "V209",
    "estimated_arrival_date": "09-10-2026",
    "total_weight": "943.000 KGS",
    "container_numbers": ["TXGU5825086"],
    "cargo_line_items": [{"description": "FIBERGLASS ROD", "weight": "943.000 KGS"}],
}
NOTICE_OLD = {**NOTICE_NEW, "prepared_date": "09-17-2026 15:17 (EDT)",
              "ams_bol_number": HOUSE}           # house number in the AMS field

BOL = {
    "document_type": "Bill of Lading",
    "master_bol_number": MASTER,
    "house_bol_number": HOUSE,
    "shipper_name": "JINSHENG TRADE (GUANGZHOU) CO.,LTD",
    "consignee_name": "WONTON FOOD CORP.",
    "total_weight": "943.00KGS",
    "container_numbers": ["TXGU5825086"],
    "cargo_line_items": [{"description": "FIBERGLASS ROD", "weight": "943.00KGS"}],
}
INVOICE = {
    "document_type": "Commercial Invoice",
    "consignee_name": "Wonton Food Corp.",
    "cargo_line_items": [
        {"description": "fiberglass rod", "pieces": "2880"},
        {"description": "fiberglass rod", "pieces": "70"},
    ],
}
PACKING = {
    "document_type": "Packing List",
    "total_weight": "943",
    "cargo_line_items": [{"description": "fiberglass rod", "weight": "921"}],
}
ISF = {
    "document_type": "ISF 10+2",
    "master_bol_number": MASTER,
    "estimated_arrival_date": "06-SEP-2026",   # forecast filed before sailing
    "hts_code": "7019909990",
    "container_numbers": ["TXGU5825086"],
}


def docs(*pairs):
    return [{"name": n, "fields": f} for n, f in pairs]


def conflicts_for(result, field):
    return [c for c in result["conflicts"] if c["field"] == field]


class TestClassify:
    def test_known_types(self):
        assert classify_document("Arrival Notice / Freight Invoice") == "arrival_notice"
        assert classify_document("Air Freight Arrival Notice") == "arrival_notice"
        assert classify_document("Sea Freight FCL Arrival Notice") == "arrival_notice"
        assert classify_document("Bill of Lading") == "bol"
        assert classify_document("Air Waybill") == "bol"
        assert classify_document("Commercial Invoice") == "invoice"
        assert classify_document("Packing List") == "packing_list"
        assert classify_document("ISF 10+2") == "isf"

    def test_unknown_and_empty(self):
        assert classify_document("Certificate of Origin") == "other"
        assert classify_document("") == "other"
        assert classify_document(None) == "other"


class TestVersionHandling:
    def test_newest_notice_wins_regardless_of_input_order(self):
        for order in ([("old", NOTICE_OLD), ("new", NOTICE_NEW)],
                      [("new", NOTICE_NEW), ("old", NOTICE_OLD)]):
            r = merge_shipment_fields(docs(*order))
            assert r["fields"]["ams_bol_number"] == MASTER
            assert r["sources"]["ams_bol_number"] == "new"

    def test_version_change_is_reported(self):
        r = merge_shipment_fields(docs(("old", NOTICE_OLD), ("new", NOTICE_NEW)))
        (c,) = conflicts_for(r, "ams_bol_number")
        assert c["kind"] == "version_change"
        assert c["chosen"] == {"value": MASTER, "source": "new"}
        assert c["others"] == [{"value": HOUSE, "source": "old"}]

    def test_undated_documents_fall_back_to_latest_added(self):
        a = {**NOTICE_NEW, "prepared_date": "", "ams_bol_number": "AAAA1"}
        b = {**NOTICE_NEW, "prepared_date": "", "ams_bol_number": "BBBB2"}
        r = merge_shipment_fields(docs(("first", a), ("second", b)))
        assert r["fields"]["ams_bol_number"] == "BBBB2"


class TestMasterBL:
    def test_master_found_and_forwarder_file_number_rejected(self):
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("bol", BOL)))
        assert r["fields"]["master_bol_number"] == MASTER

    def test_old_notice_alone_has_no_valid_master(self):
        # AMS field holds the house number; "master" is the file number.
        r = merge_shipment_fields(docs(("old", NOTICE_OLD)))
        assert r["fields"]["master_bol_number"] == ""

    def test_adding_the_bol_later_recovers_the_master(self):
        before = merge_shipment_fields(docs(("old", NOTICE_OLD)))
        after = merge_shipment_fields(docs(("old", NOTICE_OLD), ("bol", BOL)))
        assert before["fields"]["master_bol_number"] == ""
        assert after["fields"]["master_bol_number"] == MASTER
        assert after["sources"]["master_bol_number"] == "bol"

    def test_two_different_valid_masters_are_a_conflict(self):
        isf = {**ISF, "master_bol_number": "CMDUXIA1864191"}
        r = merge_shipment_fields(docs(("bol", BOL), ("isf", isf)))
        assert conflicts_for(r, "master_bol_number")


class TestConflicts:
    def test_name_variants_are_not_conflicts(self):
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("bol", BOL), ("inv", INVOICE)))
        assert conflicts_for(r, "consignee_name") == []

    def test_notice_eta_beats_isf_and_is_flagged(self):
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("isf", ISF)))
        assert r["fields"]["estimated_arrival_date"] == "09-10-2026"
        (c,) = conflicts_for(r, "estimated_arrival_date")
        assert c["kind"] == "cross_document"
        assert c["others"][0]["source"] == "isf"

    def test_weight_formatting_differences_are_not_conflicts(self):
        # 943.000 KGS vs 943.00KGS vs 943
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("bol", BOL), ("pl", PACKING)))
        assert conflicts_for(r, "total_weight") == []

    def test_net_weight_mistaken_for_total_is_flagged(self):
        net = {**PACKING, "total_weight": "893.0"}
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("pl", net)))
        assert conflicts_for(r, "total_weight")
        assert r["fields"]["total_weight"] == "943.000 KGS"   # notice still wins

    def test_unlisted_fields_never_conflict(self):
        a = {**NOTICE_NEW, "shipper_address": "Room 1"}
        b = {**BOL, "shipper_address": "Totally different"}
        r = merge_shipment_fields(docs(("n", a), ("bol", b)))
        assert conflicts_for(r, "shipper_address") == []


class TestFieldSelection:
    def test_hts_comes_from_isf(self):
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("isf", ISF)))
        assert r["fields"]["hts_code"] == "7019909990"

    def test_line_items_come_from_notice_not_invoice(self):
        r = merge_shipment_fields(docs(("inv", INVOICE), ("n", NOTICE_NEW)))
        assert r["sources"]["cargo_line_items"] == "n"
        assert len(r["fields"]["cargo_line_items"]) == 1

    def test_line_items_fall_back_to_next_document(self):
        r = merge_shipment_fields(docs(("isf", ISF), ("inv", INVOICE)))
        assert r["sources"]["cargo_line_items"] == "inv"

    def test_containers_are_unioned_and_deduplicated(self):
        extra = {**BOL, "container_numbers": ["txgu 5825086", "MSKU1234567"]}
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("bol", extra)))
        assert r["fields"]["container_numbers"] == ["TXGU5825086", "MSKU1234567"]

    def test_empty_values_never_overwrite_real_ones(self):
        blank = {**BOL, "consignee_name": "", "shipper_name": None}
        r = merge_shipment_fields(docs(("bol", blank), ("n", NOTICE_NEW)))
        assert r["fields"]["shipper_name"] == "JINSHENG TRADE (GUANGZHOU) CO.,LTD"


class TestShapeAndEdges:
    def test_empty_input(self):
        r = merge_shipment_fields([])
        assert r["conflicts"] == [] and r["fields"]["cargo_line_items"] == []

    def test_single_document_passes_through(self):
        r = merge_shipment_fields(docs(("bol", BOL)))
        assert r["fields"]["consignee_name"] == "WONTON FOOD CORP."
        assert r["conflicts"] == []

    def test_documents_summary(self):
        r = merge_shipment_fields(docs(("n", NOTICE_NEW), ("bol", BOL)))
        assert r["documents"] == [{"name": "n", "category": "arrival_notice"},
                                  {"name": "bol", "category": "bol"}]

    def test_input_not_mutated(self):
        import copy
        original = copy.deepcopy(NOTICE_NEW)
        merge_shipment_fields(docs(("n", NOTICE_NEW)))
        assert NOTICE_NEW == original


class TestEndToEnd:
    """merge -> normalize -> NetCHB mapping on the full Eagle shipment."""

    def full(self):
        return merge_shipment_fields(docs(
            ("old", NOTICE_OLD), ("new", NOTICE_NEW), ("bol", BOL),
            ("inv", INVOICE), ("pl", PACKING), ("isf", ISF)))

    def test_netchb_fields(self):
        f = map_to_netchb_fields(normalize_fields(self.full()["fields"]))
        assert f["mot"] == "10"
        assert f["mb0"] == MASTER and f["scac0"] == "EGLV"
        assert f["vNo"] == "0P52KE1MA"
        assert f["grw"] == "943"
        assert f["eArrive"] == "091026"        # notice's 09-10, not the ISF's 06-SEP
        assert f["locGoods"] == "V209"
        assert f["desc"] == "FIBERGLASS ROD"   # one line, not the invoice's two

    def test_real_conflicts_surface(self):
        fields = {c["field"] for c in self.full()["conflicts"]}
        assert "ams_bol_number" in fields and "estimated_arrival_date" in fields