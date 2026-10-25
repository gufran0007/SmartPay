from app.tests.test_tenancy import register_and_login, upload_invoice


def test_invalid_invoice_edits_are_rejected_and_values_kept(app_client):
    client = app_client()
    register_and_login(client, 'edit-validation@example.com')
    iid = upload_invoice(client)
    response = client.post(f'/update-invoice/{iid}', data={
        'invoice_number':'INV-OK', 'customer_name':'Still here', 'customer_email':'bad-email',
        'amount':'NaN', 'currency':'EUR', 'date':'2026-99-99', 'due_date':'2026-01-01',
        'customer_address':'', 'company_name':'', 'vat_id':'',
    })
    assert response.status_code == 422
    assert 'Enter a valid amount' in response.text
    assert 'Enter a valid email' in response.text
    assert 'Enter a valid date' in response.text
    assert 'Still here' in response.text
    page = client.get(f'/view-invoice/{iid}')
    assert page.context['inv'].customer_name == 'N/A'
    assert page.context['inv'].amount == 'N/A'


def test_valid_invoice_edit_can_clear_fields(app_client):
    client = app_client()
    register_and_login(client, 'edit-clear@example.com')
    iid = upload_invoice(client)
    response = client.post(f'/update-invoice/{iid}', data={
        'invoice_number':'INV-12', 'customer_name':'New Customer', 'customer_email':'',
        'amount':'25.50', 'currency':'EUR', 'date':'2026-10-01', 'due_date':'2026-10-31',
        'customer_address':'', 'company_name':'', 'vat_id':'',
    }, follow_redirects=False)
    assert response.status_code == 303
    invoice = client.get(f'/view-invoice/{iid}').context['inv']
    assert invoice.customer_name == 'New Customer'
    assert invoice.amount == '25.50'
    assert invoice.customer_email == 'N/A'
