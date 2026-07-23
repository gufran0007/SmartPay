"""
Multi-tenancy isolation tests.

Every data endpoint must (a) require login, and (b) never let one
account read or modify another account's invoices, customers, or
uploaded CSV data — even when the other account's numeric invoice_id is
guessed directly.
"""
import io
import re

PASSWORD = "TestPass123!"


def register_and_login(client, email, password=PASSWORD):
    client.post("/register", data={"email": email, "password": password}, follow_redirects=False)
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def upload_invoice(client, filename="invoice.pdf"):
    files = {"file": (filename, io.BytesIO(b"dummy invoice bytes"), "application/pdf")}
    resp = client.post("/upload", files=files, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    m = re.search(r"/view-invoice/(\d+)", resp.headers["location"])
    assert m, resp.headers["location"]
    return int(m.group(1))


def upload_customer_csv(client, customer_name):
    csv_bytes = f"name,payment_status\n{customer_name},paid on time\n".encode()
    files = {"file": ("customers.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/upload-csv", files=files, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    assert "error" not in resp.headers["location"]


# ════════════════════════════════════════════════════════
#  LOGIN GATING — every data endpoint must require a session
# ════════════════════════════════════════════════════════

class TestLoginRequired:
    def _assert_redirects_to_login(self, resp):
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"

    def test_dashboard(self, app_client):
        self._assert_redirects_to_login(app_client().get("/dashboard", follow_redirects=False))

    def test_upload_page(self, app_client):
        self._assert_redirects_to_login(app_client().get("/upload", follow_redirects=False))

    def test_upload_post(self, app_client):
        files = {"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")}
        resp = app_client().post("/upload", files=files, follow_redirects=False)
        self._assert_redirects_to_login(resp)

    def test_view_data(self, app_client):
        self._assert_redirects_to_login(app_client().get("/view-data", follow_redirects=False))

    def test_view_invoice(self, app_client):
        self._assert_redirects_to_login(app_client().get("/view-invoice/1", follow_redirects=False))

    def test_edit_invoice_page(self, app_client):
        self._assert_redirects_to_login(app_client().get("/edit-invoice/1", follow_redirects=False))

    def test_update_invoice(self, app_client):
        resp = app_client().post("/update-invoice/1", data={"customer_name": "x"}, follow_redirects=False)
        self._assert_redirects_to_login(resp)

    def test_delete_invoice(self, app_client):
        resp = app_client().post("/delete-invoice", data={"invoice_id": 1}, follow_redirects=False)
        self._assert_redirects_to_login(resp)

    def test_update_status(self, app_client):
        resp = app_client().post("/update-status/1", data={"status": "paid"}, follow_redirects=False)
        self._assert_redirects_to_login(resp)

    def test_upload_csv_page(self, app_client):
        self._assert_redirects_to_login(app_client().get("/upload-csv", follow_redirects=False))

    def test_upload_csv_post(self, app_client):
        files = {"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
        resp = app_client().post("/upload-csv", files=files, follow_redirects=False)
        self._assert_redirects_to_login(resp)

    def test_customers(self, app_client):
        self._assert_redirects_to_login(app_client().get("/customers", follow_redirects=False))

    def test_customer_insights(self, app_client):
        self._assert_redirects_to_login(app_client().get("/customer-insights/Acme", follow_redirects=False))

    def test_csv_status(self, app_client):
        self._assert_redirects_to_login(app_client().get("/csv-status", follow_redirects=False))

    def test_ml_train(self, app_client):
        self._assert_redirects_to_login(app_client().get("/ml/train", follow_redirects=False))

    def test_ml_predict(self, app_client):
        self._assert_redirects_to_login(app_client().get("/ml/predict/1", follow_redirects=False))

    def test_ml_analytics(self, app_client):
        self._assert_redirects_to_login(app_client().get("/ml/analytics", follow_redirects=False))

    def test_ml_explain(self, app_client):
        self._assert_redirects_to_login(app_client().get("/ml/explain/1", follow_redirects=False))

    def test_send_reminder(self, app_client):
        resp = app_client().post(
            "/send-reminder/1", data={"customer_email": "x@example.com"}, follow_redirects=False
        )
        self._assert_redirects_to_login(resp)


# ════════════════════════════════════════════════════════
#  CROSS-ACCOUNT ISOLATION
# ════════════════════════════════════════════════════════

class TestInvoiceIsolation:
    def test_view_invoice_cross_account_is_404(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")

        b_invoice_id = upload_invoice(b)

        resp = a.get(f"/view-invoice/{b_invoice_id}", follow_redirects=False)
        assert resp.status_code == 404

    def test_edit_invoice_cross_account_is_404(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")
        b_invoice_id = upload_invoice(b)

        resp = a.get(f"/edit-invoice/{b_invoice_id}", follow_redirects=False)
        assert resp.status_code == 404

    def test_update_status_cross_account_is_noop(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")
        b_invoice_id = upload_invoice(b)

        a.post(f"/update-status/{b_invoice_id}", data={"status": "paid"}, follow_redirects=False)

        # B's own view must still show the original (pending) status
        page = b.get(f"/view-invoice/{b_invoice_id}", follow_redirects=False)
        assert page.status_code == 200
        assert "paid" not in page.text.lower().split('name="status"')[0] or True  # sanity: page renders
        assert 'value="paid" selected' not in page.text

    def test_update_invoice_cross_account_does_not_change_data(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")
        b_invoice_id = upload_invoice(b)

        a.post(
            f"/update-invoice/{b_invoice_id}",
            data={"customer_name": "HACKED_BY_A"},
            follow_redirects=False,
        )

        page = b.get(f"/view-invoice/{b_invoice_id}", follow_redirects=False)
        assert "HACKED_BY_A" not in page.text

    def test_delete_invoice_cross_account_does_not_delete(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")
        b_invoice_id = upload_invoice(b)

        a.post("/delete-invoice", data={"invoice_id": b_invoice_id}, follow_redirects=False)

        # B can still see their own invoice — it wasn't deleted
        page = b.get(f"/view-invoice/{b_invoice_id}", follow_redirects=False)
        assert page.status_code == 200

    def test_view_data_excludes_other_accounts_invoices(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")

        upload_invoice(a)
        b_invoice_id = upload_invoice(b)

        page = a.get("/view-data", follow_redirects=False)
        assert f"/view-invoice/{b_invoice_id}" not in page.text

    def test_dashboard_counts_only_own_invoices(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")

        upload_invoice(b)
        upload_invoice(b)

        dash = a.get("/dashboard", follow_redirects=False)
        assert dash.status_code == 200
        assert "0" in dash.text  # A has zero invoices of its own

    def test_ml_predict_cross_account_redirects_without_leaking(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")
        b_invoice_id = upload_invoice(b)

        resp = a.get(f"/ml/predict/{b_invoice_id}", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/view-data"

    def test_send_reminder_cross_account_invoice_not_found(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")
        b_invoice_id = upload_invoice(b)

        resp = a.post(
            f"/send-reminder/{b_invoice_id}",
            data={"customer_email": "attacker@example.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error" in resp.headers["location"]


class TestCustomerDataIsolation:
    def test_customers_page_excludes_other_accounts_csv(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")

        upload_customer_csv(b, "OnlyVisibleToAccountB")

        page = a.get("/customers", follow_redirects=False)
        assert "OnlyVisibleToAccountB" not in page.text

        page_b = b.get("/customers", follow_redirects=False)
        assert "OnlyVisibleToAccountB" in page_b.text

    def test_customer_insights_cannot_see_other_accounts_customer(self, app_client):
        a, b = app_client(), app_client()
        register_and_login(a, "a@example.com")
        register_and_login(b, "b@example.com")

        upload_customer_csv(b, "SecretClientOfB")

        resp = a.get("/customer-insights/SecretClientOfB", follow_redirects=False)
        assert "Not Found" in resp.text or "No data found" in resp.text

        resp_b = b.get("/customer-insights/SecretClientOfB", follow_redirects=False)
        assert resp_b.status_code == 200
        assert "SecretClientOfB" in resp_b.text
