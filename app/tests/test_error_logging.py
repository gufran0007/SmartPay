"""
Stage A observability: an unhandled exception must render the friendly
error page (never a bare 500, never a leaked traceback) AND leave a full
traceback in the logs, tagged so it's one grep away in production.

This only tests the logging/error-handling plumbing added in main.py's
500 handler -- it does not assert anything about the two still-unreproduced
bugs themselves.
"""
import logging

from fastapi.testclient import TestClient

from main import app
from app.services.csv_service import CSVService


def _register_and_login(client, email="erroruser@example.com"):
    client.post("/register", data={"business_name": "Error Co", "email": email, "password": "TestPass123"})
    client.post("/login", data={"email": email, "password": "TestPass123"})


def test_unhandled_exception_logs_traceback_and_renders_friendly_page(reset_db, monkeypatch, caplog):
    # raise_server_exceptions=False: we want the *response* the real ASGI
    # server would send (friendly error page), not the exception re-raised
    # into the test itself.
    client = TestClient(app, raise_server_exceptions=False)
    _register_and_login(client)

    def _boom(self):
        raise RuntimeError("simulated failure for observability test")

    # /customers has no try/except around this call, so it reaches the
    # global 500 handler -- exactly the path that was previously silent.
    monkeypatch.setattr(CSVService, "get_all_customers", _boom)

    with caplog.at_level(logging.INFO):
        resp = client.get("/customers")

    assert resp.status_code == 500
    assert "Something went wrong" in resp.text
    assert "RuntimeError" not in resp.text
    assert "Traceback" not in resp.text

    assert "SMARTPAY_ERROR" in caplog.text
    assert "where=unhandled" in caplog.text
    assert "method=GET" in caplog.text
    assert "path=/customers" in caplog.text
    assert "user=erroruser@example.com" in caplog.text
    # the actual traceback, not just the tag line
    assert "RuntimeError: simulated failure for observability test" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text


def test_logged_exception_includes_account_id(reset_db, monkeypatch, caplog):
    client = TestClient(app, raise_server_exceptions=False)
    _register_and_login(client, email="accountcheck@example.com")

    def _boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(CSVService, "get_all_customers", _boom)

    with caplog.at_level(logging.INFO):
        client.get("/customers")

    # Whatever account_id got assigned to this fresh account, it must show
    # up in the log line -- never "account_id=None" for an authenticated request.
    assert "account_id=None" not in caplog.text
    assert "account_id=" in caplog.text


def test_upload_csv_failure_is_logged_before_redirect(reset_db, monkeypatch, caplog):
    import io
    import pandas as pd

    client = TestClient(app, raise_server_exceptions=False)
    _register_and_login(client, email="csvfail@example.com")

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated CSV processing failure")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _boom)

    files = {"file": ("history.csv", io.BytesIO(b"customer_name,amount\nAcme,10\n"), "text/csv")}
    with caplog.at_level(logging.INFO):
        resp = client.post("/upload-csv", files=files, follow_redirects=False)

    # unchanged behavior: still redirects with an error, doesn't 500
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    assert "SMARTPAY_ERROR" in caplog.text
    assert "where=upload_csv" in caplog.text
    assert "filename=history.csv" in caplog.text
    assert "RuntimeError: simulated CSV processing failure" in caplog.text
