from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient


def _client(db_session):
    from app import auth, models
    from app.database import get_db
    from app.main import app

    admin = models.User(
        username="workflow-admin",
        password_hash="not-used",
        full_name="Workflow Admin",
        role="Administrator",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[auth.require_admin] = lambda: admin
    client = TestClient(app, raise_server_exceptions=False)
    return app, client


def _clear_overrides(app):
    app.dependency_overrides.clear()


def _linked_parent_with_fee(db_session, child_count=2):
    from app import models

    parent = models.Parent(full_name="Workflow Parent", email="parent@example.test", is_active=True)
    fee = models.FeeItem(
        name="Monthly Tuition",
        category="tuition",
        frequency="monthly",
        amount=Decimal("100.00"),
        is_mandatory=False,
        is_active=True,
    )
    db_session.add_all([parent, fee])
    db_session.flush()

    learners = []
    for idx in range(child_count):
        learner = models.Learner(
            learner_code=f"20260801{idx + 1:02d}",
            full_name=f"Workflow Learner {idx + 1}",
            grade="Grade 3",
            class_name="3A",
            date_of_admission=date(2026, 8, 1),
            status="Active",
            balance=Decimal("0.00"),
            is_active=True,
        )
        db_session.add(learner)
        db_session.flush()
        db_session.add(models.LearnerParentRelationship(
            parent_id=parent.id,
            learner_id=learner.id,
            relationship_type="Parent",
            is_primary=(idx == 0),
        ))
        db_session.add(models.LearnerFeeItem(
            learner_id=learner.id,
            fee_item_id=fee.id,
            is_active=True,
        ))
        learners.append(learner)

    db_session.commit()
    return parent, learners, fee


def test_parent_invoice_can_preview_download_and_send(db_session):
    app, client = _client(db_session)
    try:
        parent, _, _ = _linked_parent_with_fee(db_session, child_count=2)

        response = client.post(
            f"/api/parents/{parent.id}/generate-invoice",
            json={
                "parent_id": parent.id,
                "due_date": "2026-08-31",
                "billing_period": "2026-08",
            },
        )
        assert response.status_code == 201, response.text
        invoice_id = response.json()["id"]

        preview = client.get(f"/api/invoices/{invoice_id}/preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "application/pdf"
        assert preview.content.startswith(b"%PDF")

        download = client.get(f"/api/invoices/{invoice_id}/pdf")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/pdf"
        assert download.content.startswith(b"%PDF")

        sent = client.post(f"/api/invoices/{invoice_id}/send", json={})
        assert sent.status_code == 200, sent.text
        assert sent.json()["recipient_email"] == "parent@example.test"
    finally:
        _clear_overrides(app)


def test_payment_receipt_pdf_and_dropdown_text(db_session):
    from app import models

    app, client = _client(db_session)
    try:
        learner = models.Learner(
            learner_code="2026080201",
            full_name="Receipt Learner",
            grade="Grade 4",
            class_name="4A",
            date_of_admission=date(2026, 8, 2),
            status="Active",
            balance=Decimal("0.00"),
            is_active=True,
        )
        db_session.add(learner)
        db_session.commit()

        created = client.post("/api/payments", json={
            "learner_id": learner.id,
            "amount_paid": "25.00",
            "date_paid": "2026-08-05",
            "payment_method": "Cash",
            "reference_number": "RCPT-WORKFLOW-1",
        })
        assert created.status_code == 201, created.text
        receipt = client.get(f"/api/payments/{created.json()['id']}/receipt")
        assert receipt.status_code == 200
        assert receipt.headers["content-type"] == "application/pdf"
        assert receipt.content.startswith(b"%PDF")

        payment_template = Path("templates/payments.html").read_text()
        create_block = payment_template.split("async function openCreatePayment()", 1)[1].split("paymentModal.show()", 1)[0]
        assert "learner_code" in create_block
        assert "Bal:" not in create_block
    finally:
        _clear_overrides(app)


def test_dashboard_activity_timestamps_are_explicit_utc(db_session):
    from app import models

    app, client = _client(db_session)
    try:
        db_session.add(models.AuditLog(
            username="workflow-admin",
            action="CREATE",
            resource_type="Learner",
            resource_id="123",
            detail="Created learner",
            created_at=datetime(2026, 8, 8, 3, 1, 21),
        ))
        db_session.commit()

        response = client.get("/api/dashboard/activity?limit=1")
        assert response.status_code == 200, response.text
        activity = response.json()
        assert activity[0]["timestamp"] == "2026-08-08T03:01:21Z"
    finally:
        _clear_overrides(app)


def test_fee_catalogue_profile_and_transport_routes(db_session):
    from app import models

    app, client = _client(db_session)
    try:
        learner = models.Learner(
            learner_code="2026080301",
            full_name="Profile Learner",
            grade="Grade 5",
            class_name="5A",
            date_of_admission=date(2026, 8, 3),
            status="Active",
            balance=Decimal("0.00"),
            is_active=True,
        )
        fee = models.FeeItem(
            name="Profile Tuition",
            category="tuition",
            frequency="monthly",
            amount=Decimal("125.00"),
            is_mandatory=True,
            is_active=True,
        )
        db_session.add_all([learner, fee])
        db_session.commit()

        assigned = client.post(f"/api/fee-items/learner/{learner.id}/auto-assign", json={})
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["assigned"] == 1

        profile = client.get(f"/api/fee-items/learner/{learner.id}/profile")
        assert profile.status_code == 200, profile.text
        assert profile.json()["learner_name"] == "Profile Learner"
        assert profile.json()["total_monthly"] == "125.00"
        assert profile.json()["assignments"][0]["fee_name"] == "Profile Tuition"

        toggled = client.put(f"/api/fee-items/{fee.id}/toggle", json={})
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["is_active"] is False

        created = client.post("/api/fee-items/transport/routes", json={
            "name": "North Route",
            "description": "Northern suburbs",
            "monthly_fee": "450.00",
        })
        assert created.status_code == 201, created.text
        route_id = created.json()["id"]

        routes = client.get("/api/fee-items/transport/routes")
        assert routes.status_code == 200
        assert routes.json()[0]["name"] == "North Route"

        updated = client.put(f"/api/fee-items/transport/routes/{route_id}", json={
            "name": "North Route Updated",
            "description": "Northern suburbs",
            "monthly_fee": "475.00",
            "is_active": True,
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["monthly_fee"] == "475.00"
    finally:
        _clear_overrides(app)


def test_csv_learner_import_skips_exact_duplicates_but_allows_same_name_with_different_birth_date(db_session):
    from app import models

    app, client = _client(db_session)
    try:
        csv_text = (
            "full_name,grade,class_name,date_of_admission,date_of_birth,status\n"
            "Same Name,Grade 2,2A,2026-08-01,2018-01-01,Active\n"
            "Same Name,Grade 2,2A,2026-08-01,2018-01-01,Active\n"
            "Same Name,Grade 2,2A,2026-08-01,2018-01-02,Active\n"
        )
        response = client.post(
            "/api/imports/learners",
            files={"file": ("learners.csv", csv_text, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] == 2
        assert body["errors"] == 1
        assert "Duplicate learner row" in body["error_report"][0]["error"]

        imported = db_session.query(models.Learner).filter(models.Learner.full_name == "Same Name").all()
        assert len(imported) == 2
        assert {learner.date_of_birth for learner in imported} == {date(2018, 1, 1), date(2018, 1, 2)}
    finally:
        _clear_overrides(app)


def test_csv_payment_import_posts_ledger_entry(db_session):
    from app import models

    app, client = _client(db_session)
    try:
        learner = models.Learner(
            learner_code="2026080401",
            full_name="Import Payment Learner",
            grade="Grade 6",
            class_name="6A",
            date_of_admission=date(2026, 8, 4),
            status="Active",
            balance=Decimal("0.00"),
            is_active=True,
        )
        db_session.add(learner)
        db_session.commit()

        csv_text = (
            "learner_code,amount_paid,date_paid,payment_method,reference_number\n"
            "2026080401,50.00,2026-08-05,Cash,IMPORT-PAY-1\n"
        )
        response = client.post(
            "/api/imports/payments",
            files={"file": ("payments.csv", csv_text, "text/csv")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] == 1

        payment = db_session.query(models.Payment).filter_by(reference_number="IMPORT-PAY-1").one()
        entry = db_session.query(models.LedgerEntry).filter_by(
            reference_type="Payment",
            reference_id=payment.id,
            transaction_type="payment",
        ).one()
        assert entry.amount == Decimal("50.00")
        assert entry.dc_indicator == "CR"
    finally:
        _clear_overrides(app)
