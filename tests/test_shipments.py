"""
Tests for the shipment tables (database.py) and shipment_service.py, using the
real Eagle documents from test_merge.py. No web server needed.
Run:  python -m pytest test_shipments.py -v
"""

import os
import tempfile

import database as db
import shipment_service as svc
from test_merge import BOL, INVOICE, ISF, NOTICE_NEW, NOTICE_OLD, MASTER


def fresh_db():
    """Point the database module at a brand-new temp file."""
    db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
    db.init_db()


def attach(shipment_id, filename, fields, file_hash=None):
    return svc.attach_extraction(
        shipment_id, file_hash or f"hash-{filename}", filename, "groq", 2, fields
    )


class TestDatabase:
    def test_create_get_and_list(self):
        fresh_db()
        sid = db.create_shipment("Wonton / Eagle")
        assert db.get_shipment(sid)["label"] == "Wonton / Eagle"
        assert db.get_shipment(9999) is None
        listed = db.list_shipments()
        assert listed[0]["id"] == sid and listed[0]["document_count"] == 0

    def test_add_document_and_roundtrip_fields(self):
        fresh_db()
        sid = db.create_shipment("s")
        fields = {"shipper_name": "上海 Trading", "container_numbers": ["TXGU5825086"]}
        doc_id, created = db.add_shipment_document(sid, "h1", "a.pdf", "groq", 2, fields)
        assert created
        (row,) = db.list_shipment_documents(sid)
        assert row["id"] == doc_id and row["filename"] == "a.pdf"
        assert row["fields"] == fields and row["extraction_version"] == 2

    def test_same_file_twice_in_one_shipment_is_ignored(self):
        fresh_db()
        sid = db.create_shipment("s")
        first, created1 = db.add_shipment_document(sid, "h1", "a.pdf", "groq", 2, {})
        again, created2 = db.add_shipment_document(sid, "h1", "a.pdf", "groq", 2, {})
        assert created1 and not created2 and first == again
        assert len(db.list_shipment_documents(sid)) == 1

    def test_same_file_in_two_shipments_is_allowed(self):
        fresh_db()
        a, b = db.create_shipment("a"), db.create_shipment("b")
        assert db.add_shipment_document(a, "h1", "x.pdf", "groq", 2, {})[1]
        assert db.add_shipment_document(b, "h1", "x.pdf", "groq", 2, {})[1]

    def test_documents_listed_in_the_order_added(self):
        fresh_db()
        sid = db.create_shipment("s")
        for n in ("one", "two", "three"):
            db.add_shipment_document(sid, n, f"{n}.pdf", "groq", 2, {})
        assert [d["filename"] for d in db.list_shipment_documents(sid)] == \
            ["one.pdf", "two.pdf", "three.pdf"]

    def test_delete_document_only_within_its_own_shipment(self):
        fresh_db()
        a, b = db.create_shipment("a"), db.create_shipment("b")
        doc_id, _ = db.add_shipment_document(a, "h1", "x.pdf", "groq", 2, {})
        assert not db.delete_shipment_document(b, doc_id)      # wrong shipment
        assert db.delete_shipment_document(a, doc_id)
        assert not db.delete_shipment_document(a, doc_id)      # already gone

    def test_delete_shipment_removes_its_documents(self):
        fresh_db()
        sid = db.create_shipment("s")
        db.add_shipment_document(sid, "h1", "x.pdf", "groq", 2, {})
        assert db.delete_shipment(sid)
        assert db.get_shipment(sid) is None
        assert db.list_shipment_documents(sid) == []
        assert not db.delete_shipment(sid)

    def test_document_count_in_listing(self):
        fresh_db()
        sid = db.create_shipment("s")
        db.add_shipment_document(sid, "h1", "x.pdf", "groq", 2, {})
        db.add_shipment_document(sid, "h2", "y.pdf", "groq", 2, {})
        assert db.list_shipments()[0]["document_count"] == 2

    def test_extraction_cache_still_works(self):
        fresh_db()
        db.save_result("k", "success", {"version": 2, "extracted_fields": {"a": 1}})
        assert db.get_cached_result("k")["result"]["extracted_fields"] == {"a": 1}
        assert db.get_cached_result("missing") is None


class TestShipmentView:
    def test_missing_shipment_is_none(self):
        fresh_db()
        assert svc.build_shipment_view(123) is None

    def test_empty_shipment_says_so(self):
        fresh_db()
        sid = db.create_shipment("s")
        view = svc.build_shipment_view(sid)
        assert view["netchb_fields"] == {}
        assert view["warnings"] == ["No documents attached yet."]
        assert view["documents"] == [] and view["conflicts"] == []

    def test_documents_arriving_over_time_improve_the_result(self):
        fresh_db()
        sid = db.create_shipment("Eagle")

        attach(sid, "notice_0917.pdf", NOTICE_OLD)
        v1 = svc.build_shipment_view(sid)
        assert v1["netchb_fields"]["mb0"] == ""                 # no valid master yet
        assert any("No valid ocean master" in w for w in v1["warnings"])

        attach(sid, "bol.pdf", BOL)                              # arrives in a later email
        v2 = svc.build_shipment_view(sid)
        assert v2["netchb_fields"]["mb0"] == MASTER
        assert v2["netchb_fields"]["scac0"] == "EGLV"
        assert v2["sources"]["master_bol_number"] == "bol.pdf"

    def test_full_eagle_set(self):
        fresh_db()
        sid = db.create_shipment("Eagle")
        for name, fields in [("old.pdf", NOTICE_OLD), ("new.pdf", NOTICE_NEW),
                             ("bol.pdf", BOL), ("inv.pdf", INVOICE), ("isf.doc", ISF)]:
            attach(sid, name, fields)
        view = svc.build_shipment_view(sid)
        f = view["netchb_fields"]
        assert f["mot"] == "10" and f["mb0"] == MASTER and f["eArrive"] == "091026"
        assert f["desc"] == "FIBERGLASS ROD"
        assert {c["field"] for c in view["conflicts"]} >= {"ams_bol_number", "estimated_arrival_date"}
        assert [d["category"] for d in view["documents"]] == \
            ["arrival_notice", "arrival_notice", "bol", "invoice", "isf"]

    def test_order_of_arrival_does_not_change_the_dated_result(self):
        fresh_db()
        a, b = db.create_shipment("a"), db.create_shipment("b")
        attach(a, "old.pdf", NOTICE_OLD); attach(a, "new.pdf", NOTICE_NEW)
        attach(b, "new.pdf", NOTICE_NEW); attach(b, "old.pdf", NOTICE_OLD)
        assert svc.build_shipment_view(a)["netchb_fields"] == \
            svc.build_shipment_view(b)["netchb_fields"]

    def test_removing_a_document_recomputes(self):
        fresh_db()
        sid = db.create_shipment("s")
        attach(sid, "old.pdf", NOTICE_OLD)
        doc_id, _ = attach(sid, "bol.pdf", BOL)
        assert svc.build_shipment_view(sid)["netchb_fields"]["mb0"] == MASTER
        db.delete_shipment_document(sid, doc_id)
        assert svc.build_shipment_view(sid)["netchb_fields"]["mb0"] == ""

    def test_duplicate_filenames_are_disambiguated_in_sources(self):
        fresh_db()
        sid = db.create_shipment("s")
        a, _ = attach(sid, "scan.pdf", NOTICE_NEW, file_hash="h-a")
        b, _ = attach(sid, "scan.pdf", {**BOL, "master_bol_number": "CMDUXIA1864191",
                                         "document_type": "Bill of Lading"}, file_hash="h-b")
        names = [d["name"] for d in svc.build_shipment_view(sid)["documents"]]
        assert names == [f"scan.pdf #{a}", f"scan.pdf #{b}"]

    def test_target_selection(self):
        fresh_db()
        sid = db.create_shipment("s")
        attach(sid, "bol.pdf", BOL)
        ace = svc.build_shipment_view(sid, "ace")
        assert "ace_fields" in ace and "netchb_fields" not in ace
        both = svc.build_shipment_view(sid, "both")
        assert "ace_fields" in both and "netchb_fields" in both

    def test_view_does_not_mutate_stored_extractions(self):
        fresh_db()
        sid = db.create_shipment("s")
        attach(sid, "new.pdf", NOTICE_NEW)
        svc.build_shipment_view(sid)
        svc.build_shipment_view(sid)
        stored = db.list_shipment_documents(sid)[0]["fields"]
        assert stored == NOTICE_NEW