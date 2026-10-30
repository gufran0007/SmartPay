import io
from app.tests.test_tenancy import register_and_login
from app.services.paths import UPLOAD_DIR
from app.services.csv_service import CSVService


def uploaded_csv_files():
    return set(UPLOAD_DIR.glob('account_*/*.csv'))


def test_csv_without_customer_column_is_rejected_and_not_saved(app_client):
    client = app_client()
    register_and_login(client, 'csv-no-customer@example.com')
    before = uploaded_csv_files()
    response = client.post('/upload-csv', files={'file': ('payments.csv', io.BytesIO(b'amount,status\n10,paid\n'), 'text/csv')}, follow_redirects=False)
    assert response.status_code == 303
    assert 'customer_name' in response.headers['location']
    assert uploaded_csv_files() == before


def test_csv_with_only_blank_customer_names_is_rejected(app_client):
    client = app_client()
    register_and_login(client, 'csv-blank-names@example.com')
    before = uploaded_csv_files()
    response = client.post('/upload-csv', files={'file': ('payments.csv', io.BytesIO(b'Customer Name,payment_status\nN/A,paid\n,late\n'), 'text/csv')}, follow_redirects=False)
    assert 'No+usable+customer+names' in response.headers['location']
    assert uploaded_csv_files() == before


def test_valid_csv_normalizes_headers_and_loads_once(app_client):
    client = app_client()
    register_and_login(client, 'csv-valid@example.com')
    response = client.post('/upload-csv', files={'file': ('payments.csv', io.BytesIO(b'Customer Name,Payment_Status,Amount\nAcme Corp,paid,125.00\n'), 'text/csv')}, follow_redirects=False)
    assert response.headers['location'] == '/dashboard?success=Customer+payment+history+imported.'
    customer_files = uploaded_csv_files()
    path = next(path for path in customer_files if path.name.endswith('_payments.csv'))
    service = CSVService(upload_dir=path.parent)
    customer = service.find_customer_match('Acme Corp')
    assert customer['total_invoices'] == 1
    assert customer['paid_on_time'] == 1
    assert customer['total_amount'] == 125.0


def test_empty_csv_returns_helpful_error(app_client):
    client = app_client()
    register_and_login(client, 'csv-empty@example.com')
    response = client.post('/upload-csv', files={'file': ('empty.csv', io.BytesIO(b''), 'text/csv')}, follow_redirects=False)
    assert 'CSV+file+is+empty' in response.headers['location']
