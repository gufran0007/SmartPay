from unittest.mock import patch
from app.tests.test_tenancy import register_and_login, upload_invoice


def test_reminder_rejects_tampered_recipient_before_smtp(app_client):
    client = app_client()
    register_and_login(client, 'tampered-reminder@example.com')
    iid = upload_invoice(client)
    client.post(f'/update-invoice/{iid}', data={
        'customer_name':'Customer', 'customer_email':'saved@example.com',
        'amount':'25', 'currency':'EUR', 'date':'2026-10-01', 'due_date':'2030-01-01',
    })
    with patch('main.smtplib.SMTP') as smtp:
        response = client.post(f'/send-reminder/{iid}', data={'customer_email':'attacker@example.com'}, follow_redirects=False)
    assert response.status_code == 303
    assert 'Update+the+invoice+email' in response.headers['location']
    smtp.assert_not_called()


def test_reminder_uses_saved_recipient_and_closes_smtp(app_client, monkeypatch):
    client = app_client()
    register_and_login(client, 'valid-reminder@example.com')
    iid = upload_invoice(client)
    client.post(f'/update-invoice/{iid}', data={
        'customer_name':'Customer', 'customer_email':'saved@example.com',
        'amount':'25', 'currency':'EUR', 'date':'2026-10-01', 'due_date':'2030-01-01',
    })
    monkeypatch.setattr('main.GMAIL_USER', 'sender@example.com')
    monkeypatch.setattr('main.GMAIL_APP_PASSWORD', 'test-only')
    with patch('main.smtplib.SMTP') as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        response = client.post(f'/send-reminder/{iid}', data={'customer_email':'SAVED@example.com'}, follow_redirects=False)
    assert response.headers['location'].endswith('?success=Reminder+sent')
    smtp.send_message.assert_called_once()
    assert smtp.send_message.call_args.args[0]['To'] == 'saved@example.com'
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with('sender@example.com', 'test-only')
