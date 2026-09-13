from app.tests.test_tenancy import register_and_login, upload_invoice


def test_database_customer_details_and_unknown_risk(app_client):
    client = app_client()
    register_and_login(client, 'customer-details@example.com')
    iid = upload_invoice(client)
    client.post(f'/update-invoice/{iid}', data={'customer_name':'Studio / North', 'amount':'150', 'currency':'EUR'})
    listing = client.get('/customers')
    assert listing.context['customers'][0]['risk_level'] == 'Not assessed'
    response = client.get('/customer-insights/Studio%20/%20North')
    assert response.status_code == 200
    assert 'Studio / North' in response.text
    assert '150 EUR' in response.text
    assert f'/view-invoice/{iid}' in response.text
    other = app_client()
    register_and_login(other, 'other-details@example.com')
    assert other.get('/customer-insights/Studio%20/%20North').status_code == 404


def test_missing_customer_returns_404(app_client):
    client = app_client()
    register_and_login(client, 'missing-details@example.com')
    assert client.get('/customer-insights/NoSuchCustomer').status_code == 404
