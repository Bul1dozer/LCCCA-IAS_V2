"""
Explicit live-server E2E financial checks.

This module is safe to import: it performs no network calls until the marked
test runs, and the test is skipped unless LCCCA_IAS_RUN_E2E=1.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
import requests


pytestmark = pytest.mark.e2e


BASE_URL = os.environ.get("LCCCA_IAS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("LCCCA_IAS_REQUEST_TIMEOUT", "10"))


def _require_disposable_e2e():
    if os.environ.get("LCCCA_IAS_RUN_E2E") != "1":
        pytest.skip("Set LCCCA_IAS_RUN_E2E=1 to run live-server E2E tests.")
    db_url = os.environ.get("LCCCA_IAS_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not any(marker in db_url for marker in ("/tmp/", "pytest", "qa", "test")):
        pytest.skip("Live E2E requires a disposable DATABASE_URL/LCCCA_IAS_DATABASE_URL.")


class E2EClient:
    def __init__(self):
        self.session = requests.Session()

    def _csrf_headers(self):
        token = self.session.cookies.get("lcca_csrf")
        return {"X-CSRF-Token": token} if token else {}

    def request(self, method, path, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if method.upper() not in {"GET", "HEAD", "OPTIONS"} and path != "/api/auth/login":
            headers.update(self._csrf_headers())
        response = self.session.request(
            method,
            f"{BASE_URL}{path}",
            timeout=REQUEST_TIMEOUT,
            headers=headers,
            **kwargs,
        )
        return response

    def login(self):
        response = self.request(
            "POST",
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert response.status_code == 200, response.text[:500]


@pytest.fixture()
def e2e_client():
    _require_disposable_e2e()
    client = E2EClient()
    health = client.request("GET", "/health")
    assert health.status_code == 200, health.text[:500]
    client.login()
    return client


def test_live_financial_integrity_smoke(e2e_client):
    suffix = uuid.uuid4().hex[:8]
    learner_response = e2e_client.request("POST", "/api/learners", json={
        "full_name": f"E2E Finance Learner {suffix}",
        "grade": "Grade 3",
        "class_name": "3A",
        "date_of_admission": "2026-01-01",
        "status": "Active",
        "physical_address": "Disposable QA Address",
    })
    assert learner_response.status_code == 201, learner_response.text[:500]
    learner = learner_response.json()

    fee_response = e2e_client.request("POST", "/api/fee-items", json={
        "name": f"E2E Tuition {suffix}",
        "category": "tuition",
        "frequency": "monthly",
        "amount": "123.45",
        "is_mandatory": False,
        "is_active": True,
    })
    assert fee_response.status_code == 201, fee_response.text[:500]
    fee = fee_response.json()

    assign_response = e2e_client.request(
        "POST",
        f"/api/fee-items/learner/{learner['id']}/assign",
        json={"fee_item_id": fee["id"]},
    )
    assert assign_response.status_code in {200, 201}, assign_response.text[:500]

    invoice_response = e2e_client.request("POST", "/api/invoices/generate", json={
        "learner_id": learner["id"],
        "due_date": "2026-08-31",
    })
    assert invoice_response.status_code == 201, invoice_response.text[:500]
    invoice = invoice_response.json()
    assert Decimal(str(invoice["current_charges"])) == Decimal("123.45")

    recon_response = e2e_client.request("GET", "/api/dashboard/reconciliation")
    assert recon_response.status_code == 200, recon_response.text[:500]
    recon = recon_response.json()
    assert Decimal(str(recon["discrepancy"])) == Decimal("0.00")
