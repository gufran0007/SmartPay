"""Regression checks for the overview and invoice lifecycle."""
from datetime import date
from unittest.mock import patch

from app.tests.test_tenancy import register_and_login, upload_invoice


def read_invoice(client, invoice_id):
    response = client.get(f'/view-invoice/{invoice_id}')
    assert response.status_code == 200
    return response.context['inv']


def test_paid_records_date_and_reopening_clears_it(app_client):
    client = app_client()
    register_and_login(client, 'lifecycle@example.com')
    iid = upload_invoice(client)
    client.post(f'/update-invoice/{iid}', data={'due_date': '2020-01-01'})
    client.post(f'/update-status/{iid}', data={'status': 'paid'})
    inv = read_invoice(client, iid)
    assert inv.status == 'paid'
    assert inv.paid_date == date.today().isoformat()
    client.post(f'/update-status/{iid}', data={'status': 'pending'})
    inv = read_invoice(client, iid)
    assert inv.status == 'overdue'
    assert inv.paid_date is None


def test_dashboard_uses_account_invoices_without_legacy_csv(app_client):
    client = app_client()
    register_and_login(client, 'overview@example.com')
    iid = upload_invoice(client)
    client.post(f'/update-invoice/{iid}', data={'customer_name':'North Studio','due_date':'2020-01-01'})
    response = client.get('/dashboard')
    assert response.status_code == 200
    assert response.context['total_invoices'] == 1
    assert response.context['total_customers'] == 1
    assert response.context['status_overdue'] == 1
    assert len(response.context['attention_invoices']) == 1


def test_same_named_uploads_have_independent_files(app_client):
    client = app_client()
    register_and_login(client, 'files@example.com')
    first = upload_invoice(client)
    second = upload_invoice(client)
    assert read_invoice(client, first).filename != read_invoice(client, second).filename


def test_paid_invoice_never_sends_email(app_client):
    client = app_client()
    register_and_login(client, 'reminders@example.com')
    iid = upload_invoice(client)
    client.post(f'/update-status/{iid}', data={'status':'paid'})
    with patch('main.smtplib.SMTP') as smtp:
        response = client.post(f'/send-reminder/{iid}', data={'customer_email':'example@example.com'}, follow_redirects=False)
    assert 'Paid+invoices' in response.headers['location']
    smtp.assert_not_called()
