import json
import os
import sqlite3
import subprocess
import sys
import textwrap


def _counts(db_path):
    con = sqlite3.connect(db_path, timeout=1)
    con.row_factory = sqlite3.Row
    try:
        counts = {}
        for table in ("invoices", "invoice_items", "ledger_entries"):
            counts[table] = con.execute(f"select count(*) as c from {table}").fetchone()["c"]
        return counts
    finally:
        con.close()


def test_parent_invoice_generation_minimal_testclient_scenario(tmp_path):
    """Regression harness for the parent-centric multi-learner invoice path.

    This intentionally runs in a subprocess so a future deadlock cannot hang
    the main pytest worker indefinitely.
    """

    db_path = tmp_path / "parent_invoice_diag.db"
    runner = tmp_path / "run_parent_invoice_diag.py"
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    runner.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import sqlite3
            import signal
            import sys
            import traceback
            from datetime import date
            from decimal import Decimal

            os.chdir({project_root!r})
            sys.path.insert(0, {project_root!r})
            os.environ["DATABASE_URL"] = "sqlite:///{db_path}"
            os.environ["LCCA_SECRET_KEY"] = "parent-invoice-diag-secret"
            os.environ["PYTHONPYCACHEPREFIX"] = "/tmp/lcca-pycache"

            class Timeout(Exception):
                pass

            def timeout_handler(signum, frame):
                raise Timeout(f"timeout at {{frame.f_code.co_filename}}:{{frame.f_lineno}} in {{frame.f_code.co_name}}")

            signal.signal(signal.SIGALRM, timeout_handler)

            def counts():
                con = sqlite3.connect({str(db_path)!r}, timeout=1)
                con.row_factory = sqlite3.Row
                try:
                    return {{
                        table: con.execute(f"select count(*) as c from {{table}}").fetchone()["c"]
                        for table in ("users", "parents", "learners", "learner_parent_relationships",
                                      "fee_items", "learner_fee_items", "invoices", "invoice_items",
                                      "ledger_entries", "audit_logs")
                    }}
                finally:
                    con.close()

            try:
                from fastapi.testclient import TestClient
                from app.main import app
                from app.database import Base, engine, SessionLocal
                from app import auth, models

                Base.metadata.create_all(bind=engine)
                db = SessionLocal()
                db.add(models.User(
                    username="admin",
                    password_hash=auth.hash_password("admin123"),
                    full_name="Diagnostic Admin",
                    role="Administrator",
                    is_active=True,
                ))
                db.commit()
                db.close()

                client = TestClient(app)

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
                    if response.status_code >= 400:
                        raise AssertionError(f"{{label}} failed: {{response.status_code}} {{response.text[:500]}}")
                    return response

                db = SessionLocal()
                parent = models.Parent(full_name="Diag Parent", is_active=True)
                fee = models.FeeItem(
                    name="Diag Tuition",
                    category="tuition",
                    frequency="monthly",
                    amount=Decimal("123.45"),
                    is_mandatory=False,
                    is_active=True,
                )
                db.add_all([parent, fee])
                db.flush()

                for idx in range(2):
                    learner = models.Learner(
                        learner_code=f"202608010{{idx + 1}}",
                        full_name=f"Diag Learner {{idx + 1}}",
                        grade="Grade 3",
                        class_name="3A",
                        date_of_admission=date(2026, 1, idx + 1),
                        status="Active",
                        is_active=True,
                    )
                    db.add(learner)
                    db.flush()
                    db.add(models.LearnerParentRelationship(
                        parent_id=parent.id,
                        learner_id=learner.id,
                        relationship_type="Parent",
                    ))
                    db.add(models.LearnerFeeItem(
                        learner_id=learner.id,
                        fee_item_id=fee.id,
                        is_active=True,
                        assigned_by="admin",
                    ))
                db.commit()
                parent_id = parent.id
                db.close()

                before_counts = counts()

                request("login", "post", "/api/auth/login", json={{"username": "admin", "password": "admin123"}})

                invoice = request(
                    "generate parent invoice",
                    "post",
                    f"/api/parents/{{parent_id}}/generate-invoice",
                    json={{"parent_id": parent_id, "due_date": "2026-08-31", "billing_period": "2026-08"}},
                ).json()

                result = {{
                    "invoice": invoice,
                    "before_counts": before_counts,
                    "counts": counts(),
                }}
                print("RESULT_JSON=" + json.dumps(result, default=str))
            except BaseException:
                traceback.print_exc()
                print("COUNTS_JSON=" + json.dumps(counts() if os.path.exists({str(db_path)!r}) else {{"exists": False}}))
                raise
            """
        )
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = project_root
    completed = subprocess.run(
        [sys.executable, str(runner)],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr

    result_line = next(
        line for line in completed.stdout.splitlines()
        if line.startswith("RESULT_JSON=")
    )
    result = json.loads(result_line.removeprefix("RESULT_JSON="))

    assert result["before_counts"]["users"] == 1
    assert result["before_counts"]["parents"] == 1
    assert result["before_counts"]["learners"] == 2
    assert result["before_counts"]["learner_parent_relationships"] == 2
    assert result["before_counts"]["fee_items"] == 1
    assert result["before_counts"]["learner_fee_items"] == 2
    assert result["before_counts"]["invoices"] == 0
    assert result["before_counts"]["invoice_items"] == 0
    assert result["before_counts"]["ledger_entries"] == 0
    assert result["invoice"]["learner_count"] == 2
    assert result["invoice"]["current_charges"] == "246.90"
    assert result["counts"]["invoices"] == 1
    assert result["counts"]["invoice_items"] == 2
    assert result["counts"]["ledger_entries"] == 2
    assert _counts(str(db_path)) == {
        "invoices": 1,
        "invoice_items": 2,
        "ledger_entries": 2,
    }
