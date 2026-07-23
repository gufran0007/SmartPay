"""
Stage 2: CSV/Excel invoice-history import.

Covers the LLM-mapping interface in isolation (fake only — no network calls
in this suite), the deterministic validation/import engine, and the full
upload -> review -> confirm HTTP flow with the LLM swapped for a fake via
FastAPI's dependency_overrides.
"""
import io
import re

import pandas as pd
import pytest

from main import app
from app.controllers import import_controller
from app.services.llm_mapping import (
    FakeColumnMapper, MappingProposal, FieldMapping, MappingProviderError,
    CANONICAL_FIELDS,
)
from app.services.date_parsing import infer_day_first, parse_canonical_date
from app.services.invoice_import import parse_canonical_amount

PASSWORD = "TestPass123!"


def register_and_login(client, email, password=PASSWORD):
    client.post("/register", data={"email": email, "password": password}, follow_redirects=False)
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


@pytest.fixture()
def use_mapper():
    def _set(mapper):
        app.dependency_overrides[import_controller._get_mapping_provider] = lambda: mapper
    yield _set
    app.dependency_overrides.pop(import_controller._get_mapping_provider, None)


def upload(client, content: bytes, filename="invoices.csv"):
    ctype = "text/csv" if filename.endswith(".csv") else "application/octet-stream"
    files = {"file": (filename, io.BytesIO(content), ctype)}
    return client.post("/import", files=files, follow_redirects=False)


def mapping_proposal(mapping: dict) -> MappingProposal:
    """mapping: {canonical_field: source_column_or_None}"""
    return MappingProposal(mappings=[
        FieldMapping(canonical_field=f, source_column=mapping.get(f), confidence=0.95 if mapping.get(f) else 0.0)
        for f in CANONICAL_FIELDS
    ])


def confirm_form(mapping: dict) -> dict:
    return {f"map_{f}": (mapping.get(f) or "") for f in CANONICAL_FIELDS}


def import_id_from_redirect(resp) -> int:
    m = re.search(r"/import/(\d+)/review", resp.headers["location"])
    assert m, resp.headers.get("location")
    return int(m.group(1))


# ════════════════════════════════════════════════════════
#  Pure unit tests: date parsing
# ════════════════════════════════════════════════════════

class TestDateParsing:
    def test_day_first_inferred_from_disambiguating_value(self):
        assert infer_day_first(["15/03/2024", "01/02/2024"]) is True

    def test_month_first_inferred_from_disambiguating_value(self):
        assert infer_day_first(["03/15/2024", "01/02/2024"]) is False

    def test_defaults_day_first_with_no_evidence(self):
        assert infer_day_first(["01/02/2024", "03/04/2024"]) is True

    def test_parses_day_first(self):
        assert parse_canonical_date("15/03/2024", day_first=True).isoformat() == "2024-03-15"

    def test_parses_month_first(self):
        assert parse_canonical_date("03/15/2024", day_first=False).isoformat() == "2024-03-15"

    def test_parses_iso(self):
        assert parse_canonical_date("2024-03-15", day_first=True).isoformat() == "2024-03-15"

    def test_parses_textual(self):
        assert parse_canonical_date("Mar 15, 2024").isoformat() == "2024-03-15"

    def test_blank_is_none(self):
        assert parse_canonical_date("") is None
        assert parse_canonical_date(None) is None
        assert parse_canonical_date("N/A") is None

    def test_garbage_is_none(self):
        assert parse_canonical_date("not a date") is None


class TestAmountParsing:
    def test_us_style(self):
        assert parse_canonical_amount("1,234.56") == 1234.56

    def test_eu_style(self):
        assert parse_canonical_amount("1.234,56") == 1234.56

    def test_currency_symbol(self):
        assert parse_canonical_amount("€1,234.56") == 1234.56

    def test_negative(self):
        assert parse_canonical_amount("-500") == -500.0

    def test_plain_integer(self):
        assert parse_canonical_amount("1500") == 1500.0

    def test_garbage_is_none(self):
        assert parse_canonical_amount("abc") is None

    def test_blank_is_none(self):
        assert parse_canonical_amount(None) is None
        assert parse_canonical_amount("") is None


# ════════════════════════════════════════════════════════
#  Fake mapper behavior
# ════════════════════════════════════════════════════════

class TestFakeColumnMapper:
    def test_naive_guess_matches_common_headers(self):
        mapper = FakeColumnMapper()
        proposal = mapper.propose_mapping(["Customer Name", "Amount", "Due Date"], [])
        by_field = {m.canonical_field: m.source_column for m in proposal.mappings}
        assert by_field["customer_name"] == "Customer Name"
        assert by_field["amount"] == "Amount"
        assert by_field["due_date"] == "Due Date"

    def test_raises_configured_error(self):
        mapper = FakeColumnMapper(error=MappingProviderError("boom"))
        with pytest.raises(MappingProviderError):
            mapper.propose_mapping(["a"], [])


# ════════════════════════════════════════════════════════
#  Full HTTP flow: upload -> review -> confirm
# ════════════════════════════════════════════════════════

class TestImportFlow:
    def test_shape_a_minimal_columns(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "a@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        csv_bytes = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-001,Acme Corp,2024-01-15,2024-02-15,1500.00,EUR\n"
        )
        resp = upload(client, csv_bytes)
        assert resp.status_code == 303, resp.text
        import_id = import_id_from_redirect(resp)

        review = client.get(f"/import/{import_id}/review", follow_redirects=False)
        assert review.status_code == 200
        assert "invoice_number" in review.text

        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200
        assert ">1<" in confirm.text or "1</div>" in confirm.text  # 1 imported

        data = client.get("/view-data", follow_redirects=False)
        assert "INV-001" in data.text
        assert "Acme Corp" in data.text

    def test_shape_b_with_customer_id_paid_date_terms(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "b@example.com")

        mapping = {
            "invoice_id": "ID", "customer_id": "Client Ref", "customer_name": "Client Name",
            "invoice_date": "Issued", "due_date": "Due", "paid_date": "Paid On",
            "amount": "Amount Due", "currency": "Currency Code", "payment_terms": "Terms",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        csv_bytes = (
            b"ID,Client Name,Client Ref,Issued,Due,Paid On,Amount Due,Currency Code,Terms\n"
            b"INV-100,Beta Ltd,CUST-9,2024-03-01,2024-04-01,2024-03-20,2500.50,USD,Net 30\n"
        )
        resp = upload(client, csv_bytes)
        import_id = import_id_from_redirect(resp)
        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200

        page = client.get("/view-data", follow_redirects=False)
        assert "Beta Ltd" in page.text

    def test_shape_c_excel_file(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "c@example.com")

        mapping = {
            "invoice_id": "Invoice#", "customer_name": "Buyer",
            "invoice_date": "DateIssued", "due_date": "DateDue",
            "amount": "Value", "currency": "Cur",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        df = pd.DataFrame([{
            "Invoice#": "INV-500", "Buyer": "Gamma Inc",
            "DateIssued": "2024-05-01", "DateDue": "2024-06-01",
            "Value": "999.99", "Cur": "GBP",
        }])
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)

        resp = client.post(
            "/import",
            files={"file": ("invoices.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        import_id = import_id_from_redirect(resp)
        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200

        page = client.get("/view-data", follow_redirects=False)
        assert "Gamma Inc" in page.text

    def test_malformed_llm_output_shows_error(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "d@example.com")
        use_mapper(FakeColumnMapper(error=MappingProviderError("LLM returned a malformed mapping: missing key")))

        resp = upload(client, b"a,b\n1,2\n")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/import?error=")

    def test_llm_timeout_shows_error(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "e@example.com")
        use_mapper(FakeColumnMapper(error=MappingProviderError("LLM request timed out: timeout")))

        resp = upload(client, b"a,b\n1,2\n")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/import?error=")

    def test_empty_file_rejected(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "f@example.com")
        use_mapper(FakeColumnMapper())

        resp = upload(client, b"")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/import?error=")

    def test_wrong_file_type_rejected(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "g@example.com")
        use_mapper(FakeColumnMapper())

        resp = upload(client, b"just some text", filename="notes.txt")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/import?error=")

    def test_duplicate_invoice_ids_within_file(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "h@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        csv_bytes = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-DUP,Acme Corp,2024-01-15,2024-02-15,1500.00,EUR\n"
            b"INV-DUP,Acme Corp,2024-01-16,2024-02-16,1600.00,EUR\n"
            b"INV-OK,Other Co,2024-01-17,2024-02-17,700.00,EUR\n"
        )
        resp = upload(client, csv_bytes)
        import_id = import_id_from_redirect(resp)
        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200
        assert "1</div>" in confirm.text or ">1<" in confirm.text  # 1 rejected as duplicate

        errors = client.get(f"/import/{import_id}/errors", follow_redirects=False)
        assert errors.status_code == 200
        assert "Duplicate invoice_id" in errors.text
        assert "INV-DUP" in errors.text

    def test_paid_date_before_invoice_date_rejected(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "i@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due", "paid_date": "Paid",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        csv_bytes = (
            b"invoice_number,Customer,Bill Date,Payment Due,Paid,Total,Curr\n"
            b"INV-BAD,Acme Corp,2024-02-15,2024-03-15,2024-01-01,1500.00,EUR\n"
        )
        resp = upload(client, csv_bytes)
        import_id = import_id_from_redirect(resp)
        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200

        errors = client.get(f"/import/{import_id}/errors", follow_redirects=False)
        assert "before invoice_date" in errors.text

    def test_missing_required_field_rejected(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "j@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        csv_bytes = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-NOCUST,,2024-01-15,2024-02-15,1500.00,EUR\n"
        )
        resp = upload(client, csv_bytes)
        import_id = import_id_from_redirect(resp)
        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200

        errors = client.get(f"/import/{import_id}/errors", follow_redirects=False)
        assert "Missing required field: customer_name" in errors.text

    def test_dd_mm_vs_mm_dd_dates(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "k@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        # Bill Date column: "15/01/2024" has day=15 > 12 -> whole column is day-first.
        csv_bytes = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-DATE1,Acme Corp,15/01/2024,01/02/2024,1000.00,EUR\n"
            b"INV-DATE2,Acme Corp,03/04/2024,01/05/2024,1000.00,EUR\n"
        )
        resp = upload(client, csv_bytes)
        import_id = import_id_from_redirect(resp)
        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200

        page = client.get("/view-data", follow_redirects=False)
        # 15/01/2024 day-first -> Jan 15; 03/04/2024 day-first (column-wide) -> Apr 3
        assert "2024-01-15" in page.text
        assert "2024-04-03" in page.text

    def test_reupload_updates_by_invoice_id(self, app_client, use_mapper):
        client = app_client()
        register_and_login(client, "l@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        first_csv = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-REUP,Acme Corp,2024-01-15,2024-02-15,1000.00,EUR\n"
        )
        resp1 = upload(client, first_csv)
        id1 = import_id_from_redirect(resp1)
        client.post(f"/import/{id1}/confirm", data=confirm_form(mapping), follow_redirects=False)

        second_csv = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-REUP,Acme Corp,2024-01-15,2024-02-15,2000.00,EUR\n"
        )
        resp2 = upload(client, second_csv)
        id2 = import_id_from_redirect(resp2)
        confirm2 = client.post(f"/import/{id2}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm2.status_code == 200
        assert "1</div>" in confirm2.text or ">1<" in confirm2.text  # updated, not imported

        page = client.get("/view-data", follow_redirects=False)
        assert page.text.count("INV-REUP") == 1  # still just one row
        assert "2000.00" in page.text

    def test_cross_account_cannot_review_or_confirm_others_import(self, app_client, use_mapper):
        a, b = app_client(), app_client()
        register_and_login(a, "m@example.com")
        register_and_login(b, "n@example.com")

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        csv_bytes = (
            b"invoice_number,Customer,Bill Date,Payment Due,Total,Curr\n"
            b"INV-SECRET,Acme Corp,2024-01-15,2024-02-15,1500.00,EUR\n"
        )
        resp = upload(b, csv_bytes)
        import_id = import_id_from_redirect(resp)

        # A guesses B's import id
        review = a.get(f"/import/{import_id}/review", follow_redirects=False)
        assert review.status_code == 404
        assert "INV-SECRET" not in review.text

        confirm = a.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        # Not found for A -> redirected back to /import, nothing imported into A's account
        assert confirm.status_code == 303
        a_data = a.get("/view-data", follow_redirects=False)
        assert "INV-SECRET" not in a_data.text
