import pandas as pd
import pytest


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
