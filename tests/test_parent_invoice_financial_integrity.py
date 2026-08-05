import json
import os
import subprocess
import sys
import textwrap
from decimal import Decimal


def _run_scenario(tmp_path, scenario, timeout=30):
    db_path = tmp_path / f"{scenario}.db"
    runner = tmp_path / f"run_{scenario}.py"
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    runner.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import signal
            import sqlite3
            import sys
            import traceback
            from datetime import date
            from decimal import Decimal

            os.chdir({project_root!r})
            sys.path.insert(0, {project_root!r})
            os.environ["DATABASE_URL"] = "sqlite:///{db_path}"
            os.environ["LCCA_SECRET_KEY"] = "financial-integrity-test-secret"
            os.environ["PYTHONPYCACHEPREFIX"] = "/tmp/lcca-pycache"

            SCENARIO = {scenario!r}

            class Timeout(Exception):
                pass

            def timeout_handler(signum, frame):
                raise Timeout(f"timeout at {{frame.f_code.co_filename}}:{{frame.f_lineno}} in {{frame.f_code.co_name}}")

            signal.signal(signal.SIGALRM, timeout_handler)

            from fastapi.testclient import TestClient
            from sqlalchemy import func
            from sqlalchemy.orm import Session as OrmSession
            from app.main import app
            from app.database import Base, engine, SessionLocal
            from app import auth, models, schemas

            Base.metadata.create_all(bind=engine)
            client = TestClient(app, raise_server_exceptions=False)

            def request(label, method, url, **kwargs):
                if method.lower() in {"post", "put", "patch", "delete"} and url != "/api/auth/login":
                    headers = dict(kwargs.pop("headers", {{}}))
                    csrf = client.cookies.get("lcca_csrf")
                    if csrf:
                        headers["X-CSRF-Token"] = csrf
                    kwargs["headers"] = headers
                signal.alarm(5)
                try:
                    response = getattr(client, method)(url, **kwargs)
                finally:
                    signal.alarm(0)
                return {{
                    "label": label,
                    "status": response.status_code,
                    "body": response.text[:1000],
                    "json": response.json() if response.headers.get("content-type", "").startswith("application/json") else None,
                }}

            def db_counts():
                db = SessionLocal()
                try:
                    return {{
                        "users": db.query(models.User).count(),
                        "parents": db.query(models.Parent).count(),
                        "learners": db.query(models.Learner).count(),
                        "relationships": db.query(models.LearnerParentRelationship).count(),
                        "fee_items": db.query(models.FeeItem).count(),
                        "learner_fee_items": db.query(models.LearnerFeeItem).count(),
                        "invoices": db.query(models.Invoice).count(),
                        "invoice_items": db.query(models.InvoiceItem).count(),
                        "ledger_entries": db.query(models.LedgerEntry).count(),
                        "audit_logs": db.query(models.AuditLog).count(),
                    }}
                finally:
                    db.close()

            def money_state():
                db = SessionLocal()
                try:
                    invoices = db.query(models.Invoice).order_by(models.Invoice.id).all()
                    items = db.query(models.InvoiceItem).order_by(models.InvoiceItem.id).all()
                    ledger = db.query(models.LedgerEntry).order_by(models.LedgerEntry.id).all()
                    counts = {{
                        "users": db.query(models.User).count(),
                        "parents": db.query(models.Parent).count(),
                        "learners": db.query(models.Learner).count(),
                        "relationships": db.query(models.LearnerParentRelationship).count(),
                        "fee_items": db.query(models.FeeItem).count(),
                        "learner_fee_items": db.query(models.LearnerFeeItem).count(),
                        "invoices": db.query(models.Invoice).count(),
                        "invoice_items": db.query(models.InvoiceItem).count(),
                        "ledger_entries": db.query(models.LedgerEntry).count(),
                        "audit_logs": db.query(models.AuditLog).count(),
                    }}
                    return {{
                        "counts": counts,
                        "invoices": [
                            {{
                                "id": inv.id,
                                "invoice_number": inv.invoice_number,
                                "parent_id": inv.parent_id,
                                "learner_id": inv.learner_id,
                                "billing_period": inv.billing_period,
                                "current_charges": str(inv.current_charges),
                                "outstanding_balance": str(inv.outstanding_balance),
                                "status": inv.status,
                            }}
                            for inv in invoices
                        ],
                        "items": [
                            {{
                                "id": item.id,
                                "invoice_id": item.invoice_id,
                                "learner_id": item.learner_id,
                                "amount": str(item.amount),
                                "description": item.description,
                            }}
                            for item in items
                        ],
                        "ledger": [
                            {{
                                "id": entry.id,
                                "learner_id": entry.learner_id,
                                "reference_id": entry.reference_id,
                                "amount": str(entry.amount),
                                "dc_indicator": entry.dc_indicator,
                                "transaction_type": entry.transaction_type,
                            }}
                            for entry in ledger
                        ],
                        "ledger_debit_total": str(sum((Decimal(str(e.amount)) for e in ledger if e.dc_indicator == "DR"), Decimal("0.00"))),
                    }}
                finally:
                    db.close()

            def add_user(username, password, role="Administrator", is_active=True):
                db = SessionLocal()
                try:
                    user = models.User(
                        username=username,
                        password_hash=auth.hash_password(password),
                        full_name=f"{{username}} User",
                        role=role,
                        is_active=is_active,
                    )
                    db.add(user)
                    db.commit()
                    return user.id
                finally:
                    db.close()

            def clear_audits():
                db = SessionLocal()
                try:
                    db.query(models.AuditLog).delete()
                    db.commit()
                finally:
                    db.close()

            def setup_parent(amounts, parent_name="QA Parent"):
                db = SessionLocal()
                try:
                    parent = models.Parent(full_name=parent_name, is_active=True)
                    db.add(parent)
                    db.flush()
                    learner_ids = []
                    fee_ids = []
                    for idx, amount in enumerate(amounts, start=1):
                        learner = models.Learner(
                            learner_code=f"20260801{{idx:02d}}",
                            full_name=f"QA Learner {{idx}}",
                            grade=f"Grade {{idx}}",
                            class_name=f"{{idx}}A",
                            date_of_admission=date(2026, 1, idx),
                            status="Active",
                            is_active=True,
                        )
                        fee = models.FeeItem(
                            name=f"QA Fee {{idx}}",
                            category="tuition",
                            frequency="monthly",
                            amount=Decimal(str(amount)),
                            is_mandatory=False,
                            is_active=True,
                        )
                        db.add_all([learner, fee])
                        db.flush()
                        db.add(models.LearnerParentRelationship(
                            learner_id=learner.id,
                            parent_id=parent.id,
                            relationship_type="Parent",
                        ))
                        db.add(models.LearnerFeeItem(
                            learner_id=learner.id,
                            fee_item_id=fee.id,
                            is_active=True,
                            assigned_by="qa",
                        ))
                        learner_ids.append(learner.id)
                        fee_ids.append(fee.id)
                    db.commit()
                    return {{"parent_id": parent.id, "learner_ids": learner_ids, "fee_ids": fee_ids}}
                finally:
                    db.close()

            def login(username="admin", password="admin123"):
                return request("login", "post", "/api/auth/login", json={{"username": username, "password": password}})

            def generate(parent_id):
                return request(
                    "generate parent invoice",
                    "post",
                    f"/api/parents/{{parent_id}}/generate-invoice",
                    json={{"parent_id": parent_id, "due_date": "2026-08-31", "billing_period": "2026-08"}},
                )

            def patch_audit_failure():
                import app.routers.parents as parents_router
                def fail_audit(*args, **kwargs):
                    raise RuntimeError("controlled before-commit audit failure")
                parents_router.audit_from_request = fail_audit

            def patch_failure_before_flush():
                import app.ledger as ledger
                def fail_next_invoice_number(*args, **kwargs):
                    raise RuntimeError("controlled before-flush failure")
                ledger.next_invoice_number = fail_next_invoice_number

            def patch_invoice_item_failure():
                original = models.InvoiceItem.__init__
                def failing_invoice_item_init(self, *args, **kwargs):
                    raise RuntimeError("controlled after-invoice-flush failure")
                models.InvoiceItem.__init__ = failing_invoice_item_init

            def patch_ledger_start_failure():
                import app.ledger as ledger
                def failing_post_invoice(*args, **kwargs):
                    raise RuntimeError("controlled after-item-creation failure")
                ledger.post_invoice = failing_post_invoice

            def patch_final_ledger_failure(expected_calls):
                import app.ledger as ledger
                original = ledger.post_invoice
                calls = {{"count": 0}}
                def failing_post_invoice(*args, **kwargs):
                    calls["count"] += 1
                    entry = original(*args, **kwargs)
                    if calls["count"] == expected_calls:
                        raise RuntimeError("controlled after-flush before-commit failure")
                    return entry
                ledger.post_invoice = failing_post_invoice

            def patch_refresh_failure_after_commit():
                original = OrmSession.refresh
                def failing_refresh(self, instance, *args, **kwargs):
                    if instance.__class__.__name__ == "Invoice":
                        raise RuntimeError("controlled post-commit response failure")
                    return original(self, instance, *args, **kwargs)
                OrmSession.refresh = failing_refresh

            def patch_response_validation_failure():
                import app.routers.parents as parents_router
                def fail_response_validation(*args, **kwargs):
                    raise RuntimeError("controlled response DTO failure")
                parents_router._validate_parent_invoice_response = fail_response_validation

            def patch_commit_failure():
                def failing_commit(self):
                    raise RuntimeError("controlled commit failure")
                OrmSession.commit = failing_commit

            def run():
                if SCENARIO == "three_learners":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99", "0.01"])
                    login()
                    clear_audits()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "duplicate_invoice_request":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    first = generate(data["parent_id"])
                    after_first = money_state()
                    second = generate(data["parent_id"])
                    return {{"setup": data, "first": first, "after_first": after_first, "second": second, "state": money_state()}}

                if SCENARIO == "partial_duplicate":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99", "0.01"])
                    from app.fee_engine import generate_invoice_for_learner
                    db = SessionLocal()
                    try:
                        learner = db.query(models.Learner).filter(models.Learner.id == data["learner_ids"][0]).one()
                        generate_invoice_for_learner(
                            db=db,
                            learner=learner,
                            due_date=date(2026, 8, 31),
                            billing_period="2026-08",
                            triggered_by="pre-invoice",
                        )
                        db.commit()
                    finally:
                        db.close()
                    login()
                    clear_audits()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "failure_before_commit":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_audit_failure()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "failure_before_flush":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_failure_before_flush()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "failure_after_invoice_flush":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_invoice_item_failure()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "failure_after_invoice_item_creation":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_ledger_start_failure()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "failure_after_flush_before_commit":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_final_ledger_failure(expected_calls=2)
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "post_commit_response_failure":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_refresh_failure_after_commit()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "response_dto_failure_before_commit":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_response_validation_failure()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "commit_failure":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login()
                    clear_audits()
                    patch_commit_failure()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "decimal_precision":
                    add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99", "0.01"])
                    login()
                    clear_audits()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "response": response, "state": money_state()}}

                if SCENARIO == "zero_and_negative_values":
                    add_user("admin", "admin123")
                    zero_data = setup_parent(["0.00"])
                    login()
                    clear_audits()
                    zero_response = generate(zero_data["parent_id"])
                    zero_state = money_state()
                    negative_schema_accepted = True
                    try:
                        schemas.FeeItemCreate(name="Negative Fee", category="tuition", frequency="monthly", amount=Decimal("-1.00"))
                    except Exception:
                        negative_schema_accepted = False
                    negative_response = request("create negative fee", "post", "/api/fee-items", json={{
                        "name": "Negative Fee",
                        "category": "tuition",
                        "frequency": "monthly",
                        "amount": "-1.00",
                        "is_mandatory": False,
                    }})
                    return {{
                        "zero_response": zero_response,
                        "zero_state": zero_state,
                        "negative_schema_accepted": negative_schema_accepted,
                        "negative_response": negative_response,
                        "state": money_state(),
                    }}

                if SCENARIO == "fee_validation_values":
                    add_user("admin", "admin123")
                    login()
                    clear_audits()
                    responses = []
                    for value in ["0", "-0.01", "-100", "0.01", "123.45", "9999999999.99"]:
                        responses.append(request("create fee", "post", "/api/fee-items", json={{
                            "name": f"Fee {{value}}",
                            "category": "tuition",
                            "frequency": "monthly",
                            "amount": value,
                            "is_mandatory": False,
                        }}))
                    return {{"responses": responses, "state": money_state()}}

                if SCENARIO.startswith("auth_"):
                    role = "Administrator"
                    username = "admin"
                    password = "admin123"
                    active = True
                    if SCENARIO == "auth_non_admin":
                        role = "Clerk"
                        username = "clerk"
                        password = "clerk123"
                    if SCENARIO == "auth_disabled_user":
                        active = False
                        username = "disabled"
                        password = "disabled123"
                    if SCENARIO != "auth_unauthenticated" and SCENARIO != "auth_invalid_session":
                        add_user(username, password, role=role, is_active=active)
                    else:
                        add_user("admin", "admin123")
                    data = setup_parent(["123.45", "99.99"])
                    login_response = None
                    if SCENARIO == "auth_invalid_session":
                        client.cookies.set("lcca_session", "not-a-valid-session")
                    elif SCENARIO != "auth_unauthenticated":
                        login_response = login(username, password)
                    clear_audits()
                    response = generate(data["parent_id"])
                    return {{"setup": data, "login": login_response, "response": response, "state": money_state()}}

                raise AssertionError(f"unknown scenario {{SCENARIO}}")

            try:
                result = run()
                print("RESULT_JSON=" + json.dumps(result, default=str))
            except BaseException:
                traceback.print_exc()
                print("RESULT_JSON=" + json.dumps({{"exception": traceback.format_exc(), "state": money_state()}}, default=str))
                raise
            """
        )
    )

    completed = subprocess.run(
        [sys.executable, str(runner)],
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": project_root},
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result_line = next(line for line in completed.stdout.splitlines() if line.startswith("RESULT_JSON="))
    return json.loads(result_line.removeprefix("RESULT_JSON="))


def _amounts_by_learner(items):
    return {item["learner_id"]: item["amount"] for item in items}


def test_parent_invoice_three_learners_integrity(tmp_path):
    result = _run_scenario(tmp_path, "three_learners")

    assert result["response"]["status"] == 201
    state = result["state"]
    assert state["counts"]["invoices"] == 1
    assert state["counts"]["invoice_items"] == 3
    assert state["counts"]["ledger_entries"] == 3
    assert state["invoices"][0]["current_charges"] == "223.45"
    assert state["ledger_debit_total"] == "223.45"
    assert set(_amounts_by_learner(state["items"])) == set(result["setup"]["learner_ids"])


def test_duplicate_parent_invoice_request_does_not_duplicate_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "duplicate_invoice_request")

    assert result["first"]["status"] == 201
    assert result["second"]["status"] == 400
    assert "already been invoiced" in result["second"]["body"]
    assert result["after_first"]["counts"]["invoices"] == 1
    assert result["state"]["counts"]["invoices"] == 1
    assert result["state"]["counts"]["invoice_items"] == 2
    assert result["state"]["counts"]["ledger_entries"] == 2
    assert result["state"]["ledger_debit_total"] == "223.44"


def test_parent_invoice_skips_previously_invoiced_sibling_only(tmp_path):
    result = _run_scenario(tmp_path, "partial_duplicate")

    assert result["response"]["status"] == 201
    parent_invoice = result["response"]["json"]
    assert parent_invoice["learner_count"] == 2
    assert parent_invoice["current_charges"] == "100.00"
    assert result["state"]["counts"]["invoices"] == 2
    assert result["state"]["counts"]["invoice_items"] == 3
    assert result["state"]["counts"]["ledger_entries"] == 3

    parent_invoice_id = parent_invoice["id"]
    parent_items = [item for item in result["state"]["items"] if item["invoice_id"] == parent_invoice_id]
    assert {item["learner_id"] for item in parent_items} == set(result["setup"]["learner_ids"][1:])


def test_failure_before_commit_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "failure_before_commit")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_failure_before_flush_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "failure_before_flush")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_failure_after_invoice_flush_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "failure_after_invoice_flush")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_failure_after_invoice_item_creation_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "failure_after_invoice_item_creation")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_failure_after_flush_before_commit_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "failure_after_flush_before_commit")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_successful_commit_does_not_require_post_commit_refresh(tmp_path):
    result = _run_scenario(tmp_path, "post_commit_response_failure")

    assert result["response"]["status"] == 201
    assert result["state"]["counts"]["invoices"] == 1
    assert result["state"]["counts"]["invoice_items"] == 2
    assert result["state"]["counts"]["ledger_entries"] == 2
    assert result["state"]["counts"]["audit_logs"] == 1
    assert result["state"]["ledger_debit_total"] == "223.44"


def test_response_dto_failure_before_commit_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "response_dto_failure_before_commit")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_commit_failure_rolls_back_financial_rows(tmp_path):
    result = _run_scenario(tmp_path, "commit_failure")

    assert result["response"]["status"] == 500
    assert result["state"]["counts"]["invoices"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 0


def test_decimal_precision_for_parent_invoice_totals(tmp_path):
    result = _run_scenario(tmp_path, "decimal_precision")

    assert result["response"]["status"] == 201
    assert result["response"]["json"]["current_charges"] == "223.45"
    assert result["state"]["ledger_debit_total"] == "223.45"
    assert sorted(Decimal(item["amount"]) for item in result["state"]["items"]) == [
        Decimal("0.01"),
        Decimal("99.99"),
        Decimal("123.45"),
    ]


def test_zero_and_negative_fee_values_current_behavior(tmp_path):
    result = _run_scenario(tmp_path, "zero_and_negative_values")

    assert result["zero_response"]["status"] == 400
    assert result["zero_state"]["counts"]["invoices"] == 0
    assert result["zero_state"]["counts"]["invoice_items"] == 0
    assert result["zero_state"]["counts"]["ledger_entries"] == 0
    assert result["negative_schema_accepted"] is False
    assert result["negative_response"]["status"] in (400, 422)


def test_fee_item_amount_validation_values(tmp_path):
    result = _run_scenario(tmp_path, "fee_validation_values")

    statuses = [response["status"] for response in result["responses"]]
    assert all(status in (400, 422) for status in statuses[:3])
    assert statuses[3:] == [201, 201, 201]
    assert result["state"]["counts"]["fee_items"] == 3
    assert result["state"]["counts"]["learner_fee_items"] == 0
    assert result["state"]["counts"]["invoice_items"] == 0
    assert result["state"]["counts"]["ledger_entries"] == 0
    assert result["state"]["counts"]["audit_logs"] == 3


def test_parent_invoice_authorization_matrix(tmp_path):
    scenarios = {
        "auth_unauthenticated": 401,
        "auth_non_admin": 403,
        "auth_admin": 201,
        "auth_disabled_user": 401,
        "auth_invalid_session": 401,
    }

    results = {name: _run_scenario(tmp_path, name) for name in scenarios}

    for name, expected_status in scenarios.items():
        assert results[name]["response"]["status"] == expected_status
        if expected_status != 201:
            assert results[name]["state"]["counts"]["invoices"] == 0
            assert results[name]["state"]["counts"]["invoice_items"] == 0
            assert results[name]["state"]["counts"]["ledger_entries"] == 0
        else:
            assert results[name]["state"]["counts"]["invoices"] == 1
            assert results[name]["state"]["counts"]["invoice_items"] == 2
            assert results[name]["state"]["counts"]["ledger_entries"] == 2

    assert results["auth_disabled_user"]["login"]["status"] == 401
