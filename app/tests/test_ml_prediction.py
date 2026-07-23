"""
Stage 3: per-tenant prediction.

Every test builds paid invoices directly in the DB (bypassing the Stage 2
import pipeline, which has its own test file) so exact scenarios — cold
start, exactly-at-threshold, two accounts with different data — are easy
to construct precisely.
"""
import io
from datetime import date

import pytest

from main import app
from app.controllers import import_controller
from app.models.database import SessionLocal, Account, Invoice, User
from app.services.ml_service import MLService, MODE_ML, MODE_HEURISTIC, MIN_TRAINING_INVOICES

PASSWORD = "TestPass123!"


@pytest.fixture()
def use_mapper():
    def _set(mapper):
        app.dependency_overrides[import_controller._get_mapping_provider] = lambda: mapper
    yield _set
    app.dependency_overrides.pop(import_controller._get_mapping_provider, None)


def make_account(db, name="TestCo") -> Account:
    account = Account(name=name)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def make_paid_invoice(db, account_id, customer_name, invoice_date, due_date, paid_date, amount=1000.0):
    inv = Invoice(
        account_id=account_id,
        invoice_number=f"INV-{customer_name}-{invoice_date.isoformat()}",
        customer_name=customer_name,
        amount=str(amount),
        currency="EUR",
        date=invoice_date.isoformat(),
        due_date=due_date.isoformat(),
        paid_date=paid_date.isoformat() if paid_date else None,
        status="paid" if paid_date else "pending",
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def seed_mixed_outcomes(db, account_id, count, prefix="Cust"):
    """`count` paid invoices for distinct customers, alternating on-time
    (paid before due) and late (paid after due) so both classes exist."""
    for i in range(count):
        paid = date(2024, 1, 20) if i % 2 == 0 else date(2024, 2, 15)
        make_paid_invoice(db, account_id, f"{prefix}{i}", date(2024, 1, 1), date(2024, 1, 31), paid)


class TestColdStart:
    def test_no_paid_invoices_stays_on_heuristic(self, reset_db):
        db = SessionLocal()
        try:
            account = make_account(db)
            ml_service = MLService(account.id)
            count, message = ml_service.train(db)

            assert count == 0
            assert ml_service.model is None
            assert "heuristic" in message.lower()

            invoice = make_paid_invoice(db, account.id, "First Customer", date(2024, 1, 1), date(2024, 2, 1), None)
            result = ml_service.predict(db, invoice)
            assert result.mode == MODE_HEURISTIC
            assert result.invoices_seen == 0
            assert result.held_out_accuracy is None
        finally:
            db.close()

    def test_cold_start_never_reports_a_held_out_accuracy(self, reset_db):
        db = SessionLocal()
        try:
            account = make_account(db)
            invoice = make_paid_invoice(db, account.id, "Solo Customer", date(2024, 1, 1), date(2024, 2, 1), None)
            result = MLService(account.id).predict(db, invoice)
            assert result.held_out_accuracy is None  # never a made-up number
        finally:
            db.close()


class TestThresholdBoundary:
    def test_one_below_threshold_is_heuristic(self, reset_db):
        db = SessionLocal()
        try:
            account = make_account(db)
            seed_mixed_outcomes(db, account.id, MIN_TRAINING_INVOICES - 1)
            ml_service = MLService(account.id)
            count, message = ml_service.train(db)

            assert count == MIN_TRAINING_INVOICES - 1
            assert ml_service.model is None
            assert str(MIN_TRAINING_INVOICES) in message
        finally:
            db.close()

    def test_exactly_at_threshold_trains_ml(self, reset_db):
        db = SessionLocal()
        try:
            account = make_account(db)
            seed_mixed_outcomes(db, account.id, MIN_TRAINING_INVOICES)
            ml_service = MLService(account.id)
            count, message = ml_service.train(db)

            assert count == MIN_TRAINING_INVOICES
            assert ml_service.model is not None
            assert "trained on" in message.lower()
        finally:
            db.close()


class TestUnknownCustomer:
    def test_ml_mode_predicts_for_a_customer_with_no_history(self, reset_db):
        db = SessionLocal()
        try:
            account = make_account(db)
            seed_mixed_outcomes(db, account.id, MIN_TRAINING_INVOICES)
            ml_service = MLService(account.id)
            ml_service.train(db)
            assert ml_service.model is not None

            new_invoice = make_paid_invoice(db, account.id, "Brand New Co", date(2024, 3, 1), date(2024, 3, 31), None)
            result = ml_service.predict(db, new_invoice)
            assert result.mode == MODE_ML
            assert 0.0 <= result.probability <= 1.0
            assert result.label in (0, 1)
        finally:
            db.close()

    def test_heuristic_mode_predicts_for_a_customer_with_no_history(self, reset_db):
        db = SessionLocal()
        try:
            account = make_account(db)  # cold start -> heuristic
            new_invoice = make_paid_invoice(db, account.id, "Nobody Yet", date(2024, 3, 1), date(2024, 3, 8), None)
            result = MLService(account.id).predict(db, new_invoice)
            assert result.mode == MODE_HEURISTIC
            assert result.label == 1  # 7-day terms -> flagged risky by the heuristic
        finally:
            db.close()


class TestModelIsolationBetweenAccounts:
    def test_separate_model_files_and_training_data(self, reset_db):
        db = SessionLocal()
        try:
            account_a = make_account(db, "AccountA")
            account_b = make_account(db, "AccountB")

            seed_mixed_outcomes(db, account_a.id, MIN_TRAINING_INVOICES, prefix="A-Cust")
            seed_mixed_outcomes(db, account_b.id, 5, prefix="B-Cust")  # stays below threshold

            ml_a = MLService(account_a.id)
            count_a, _ = ml_a.train(db)
            ml_b = MLService(account_b.id)
            count_b, _ = ml_b.train(db)

            assert count_a == MIN_TRAINING_INVOICES
            assert count_b == 5
            assert ml_a.model is not None
            assert ml_b.model is None
            assert ml_a.model_path != ml_b.model_path
            assert ml_a.model_path.exists()
            assert not ml_b.model_path.exists()

            # A fresh instance for B must not somehow load A's model, and
            # its live paid-invoice count (heuristic mode has no saved
            # "trained_on" -- that field only means something once a model
            # is actually persisted) must reflect only B's own invoices.
            ml_b_fresh = MLService(account_b.id)
            assert ml_b_fresh.model is None
            assert ml_b_fresh.get_model_info(db)["invoices_seen"] == 5
        finally:
            db.close()

    def test_predicting_does_not_cross_contaminate(self, reset_db):
        db = SessionLocal()
        try:
            account_a = make_account(db, "AccountC")
            account_b = make_account(db, "AccountD")
            seed_mixed_outcomes(db, account_a.id, MIN_TRAINING_INVOICES, prefix="C-Cust")
            MLService(account_a.id).train(db)

            # Account D has never been trained -- predicting for it must use
            # the heuristic, never account C's model.
            invoice_d = make_paid_invoice(db, account_b.id, "D Customer", date(2024, 1, 1), date(2024, 1, 8), None)
            result = MLService(account_b.id).predict(db, invoice_d)
            assert result.mode == MODE_HEURISTIC
        finally:
            db.close()


class TestRetrainAfterImport:
    def test_confirmed_import_retrains_automatically(self, app_client, use_mapper):
        from app.services.llm_mapping import FakeColumnMapper, MappingProposal, FieldMapping, CANONICAL_FIELDS

        def mapping_proposal(mapping):
            return MappingProposal(mappings=[
                FieldMapping(canonical_field=f, source_column=mapping.get(f), confidence=0.9 if mapping.get(f) else 0.0)
                for f in CANONICAL_FIELDS
            ])

        def confirm_form(mapping):
            return {f"map_{f}": (mapping.get(f) or "") for f in CANONICAL_FIELDS}

        client = app_client()
        client.post("/register", data={"email": "retrain@example.com", "password": PASSWORD}, follow_redirects=False)
        client.post("/login", data={"email": "retrain@example.com", "password": PASSWORD}, follow_redirects=False)

        mapping = {
            "invoice_id": "invoice_number", "customer_name": "Customer",
            "invoice_date": "Bill Date", "due_date": "Payment Due", "paid_date": "Paid",
            "amount": "Total", "currency": "Curr",
        }
        use_mapper(FakeColumnMapper(response=mapping_proposal(mapping)))

        rows = []
        for i in range(MIN_TRAINING_INVOICES):
            paid = "2024-01-20" if i % 2 == 0 else "2024-02-15"
            rows.append(f"INV-{i},Cust{i},2024-01-01,2024-01-31,{paid},1000.00,EUR")
        csv_bytes = ("invoice_number,Customer,Bill Date,Payment Due,Paid,Total,Curr\n" + "\n".join(rows) + "\n").encode()

        files = {"file": ("invoices.csv", io.BytesIO(csv_bytes), "text/csv")}
        resp = client.post("/import", files=files, follow_redirects=False)
        assert resp.status_code == 303, resp.text
        import re
        import_id = int(re.search(r"/import/(\d+)/review", resp.headers["location"]).group(1))

        confirm = client.post(f"/import/{import_id}/confirm", data=confirm_form(mapping), follow_redirects=False)
        assert confirm.status_code == 200

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "retrain@example.com").first()
            account_id = user.account_id
        finally:
            db.close()

        # A brand-new MLService instance loads from disk -- this proves
        # training happened as part of the confirm request, not something
        # this test triggered itself.
        ml_service = MLService(account_id)
        assert ml_service.model is not None
        assert ml_service.trained_on == MIN_TRAINING_INVOICES
