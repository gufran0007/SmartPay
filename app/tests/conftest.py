import os
import tempfile
from pathlib import Path

# Must happen before `main`/`app.models.database` is imported by any test
# (conftest.py is collected first), so integration tests never touch the
# real dev database or uploads/ directory.
_test_root = Path(tempfile.mkdtemp(prefix="smartpay_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_test_root / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_test_root / "uploads")
os.environ.setdefault("SESSION_SECRET", "test-secret-for-pytest-only")
os.environ.setdefault("GMAIL_USER", "test@example.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "test-password")

import pandas as pd
import pytest


@pytest.fixture()
def reset_db():
    """Fresh schema for tests that exercise the real app/DB end-to-end."""
    from app.models.database import Base, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def app_client(reset_db):
    """Factory for independent TestClient sessions against the same app —
    each call gets its own cookie jar, so two logged-in accounts in the
    same test never share a session."""
    from fastapi.testclient import TestClient
    from main import app

    clients = []

    def _make():
        c = TestClient(app)
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.close()


@pytest.fixture
def good_customer():
    """A reliable customer: high on-time rate, no unpaid invoices, low delay."""
    return {
        'total_invoices': 20,
        'paid_on_time': 19,
        'paid_late': 1,
        'not_paid': 0,
        'total_amount': 40000.0,
        'avg_payment_delay': 2.0,
        'risk_factors': [],
    }


@pytest.fixture
def bad_customer():
    """An unreliable customer: low on-time rate, unpaid invoices, long delays."""
    return {
        'total_invoices': 10,
        'paid_on_time': 2,
        'paid_late': 4,
        'not_paid': 4,
        'total_amount': 20000.0,
        'avg_payment_delay': 40.0,
        'risk_factors': ['Disputed invoice', 'Declining profit margin', 'Negative cash flow'],
    }


@pytest.fixture
def sample_csv():
    """3 customers, used to test CSV loading/aggregation."""
    return pd.DataFrame([
        {'Customer_Name': 'Alice Corp', 'late_payment': 1, 'amount': 1000},
        {'Customer_Name': 'Alice Corp', 'late_payment': 0, 'amount': 2000},
        {'Customer_Name': 'Bob LLC', 'late_payment': 0, 'amount': 2500},
        {'Customer_Name': 'Bob LLC', 'late_payment': 0, 'amount': 3000},
        {'Customer_Name': 'Carol Inc', 'late_payment': 0, 'amount': 500},
    ])


@pytest.fixture
def invoice_text():
    """A synthetic EUR invoice with one clean invoice-number match and two dates."""
    return (
        "Invoice #INV-2024-1001\n"
        "Bill To\n"
        "John Smith\n"
        "john@example.com\n"
        "Invoice Date: 01/03/2024\n"
        "Due Date: 15/03/2024\n"
        "Total: €1,250.00\n"
    )
