from datetime import date
from decimal import Decimal
import uuid


def _suffix():
    return uuid.uuid4().hex[:8]


def _create_parent(db_session, name):
    from app import models

    parent = models.Parent(full_name=name, email=f"{name.replace(' ', '.').lower()}@example.test")
    db_session.add(parent)
    db_session.flush()
    return parent


def _create_learner(db_session, name, code, parent):
    from app import models

    learner = models.Learner(
        learner_code=code,
        full_name=name,
        grade="Grade 3",
        class_name="3A",
        date_of_admission=date(2026, 8, 1),
        status="Active",
        balance=Decimal("0.00"),
    )
    db_session.add(learner)
    db_session.flush()
    db_session.add(models.LearnerParentRelationship(
        learner_id=learner.id,
        parent_id=parent.id,
        relationship_type="Parent",
        is_primary=True,
    ))
    db_session.flush()
    return learner


def test_parent_invoice_listing_exposes_parent_and_linked_learners(db_session):
    from app import models
    from app.routers.invoices import _to_out

    marker = _suffix()
    parent = _create_parent(db_session, f"RC Parent {marker}")
    learner_a = _create_learner(db_session, f"RC Learner A {marker}", f"RCA{marker}", parent)
    learner_b = _create_learner(db_session, f"RC Learner B {marker}", f"RCB{marker}", parent)
    invoice = models.Invoice(
        invoice_number=f"INV-RC-PARENT-{marker}",
        parent_id=parent.id,
        learner_id=None,
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        billing_period="2026-08",
        previous_balance=Decimal("10.00"),
        current_charges=Decimal("300.00"),
        payments_made=Decimal("25.00"),
        outstanding_balance=Decimal("285.00"),
        status="Generated",
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add_all([
        models.InvoiceItem(
            invoice_id=invoice.id,
            learner_id=learner_a.id,
            description="Tuition A",
            amount=Decimal("100.00"),
        ),
        models.InvoiceItem(
            invoice_id=invoice.id,
            learner_id=learner_b.id,
            description="Tuition B",
            amount=Decimal("200.00"),
        ),
    ])
    db_session.commit()

    db_session.refresh(invoice)
    output = _to_out(invoice)

    assert output.parent_name == parent.full_name
    assert output.learner_name is None
    assert output.billing_period == "2026-08"
    assert output.linked_learner_names == [learner_a.full_name, learner_b.full_name]
    assert {item.learner_name for item in output.items} == {learner_a.full_name, learner_b.full_name}


def test_legacy_learner_invoice_listing_keeps_parent_context(db_session):
    from app import models
    from app.routers.invoices import _to_out

    marker = _suffix()
    parent = _create_parent(db_session, f"RC Legacy Parent {marker}")
    learner = _create_learner(db_session, f"RC Legacy Learner {marker}", f"RCL{marker}", parent)
    invoice = models.Invoice(
        invoice_number=f"INV-RC-LEGACY-{marker}",
        learner_id=learner.id,
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        billing_period="2026-08",
        previous_balance=Decimal("0.00"),
        current_charges=Decimal("123.45"),
        payments_made=Decimal("0.00"),
        outstanding_balance=Decimal("123.45"),
        status="Generated",
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(models.InvoiceItem(
        invoice_id=invoice.id,
        learner_id=learner.id,
        description="Legacy charge",
        amount=Decimal("123.45"),
    ))
    db_session.commit()

    db_session.refresh(invoice)
    output = _to_out(invoice)

    assert output.parent_name == parent.full_name
    assert output.learner_name == learner.full_name
    assert output.linked_learner_names == [learner.full_name]


def test_invoice_api_search_matches_parent_name_and_invoice_number(db_session):
    from app import models
    from app.routers.invoices import list_invoices

    marker = _suffix()
    parent = _create_parent(db_session, f"RC Search Parent {marker}")
    learner = _create_learner(db_session, f"RC Search Learner {marker}", f"RCS{marker}", parent)
    invoice = models.Invoice(
        invoice_number=f"INV-RC-SEARCH-{marker}",
        parent_id=parent.id,
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        billing_period="2026-08",
        previous_balance=Decimal("0.00"),
        current_charges=Decimal("50.00"),
        payments_made=Decimal("0.00"),
        outstanding_balance=Decimal("50.00"),
        status="Generated",
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(models.InvoiceItem(
        invoice_id=invoice.id,
        learner_id=learner.id,
        description="Search charge",
        amount=Decimal("50.00"),
    ))
    db_session.commit()

    by_parent = list_invoices(search=f"Search Parent {marker}", db=db_session)
    by_number = list_invoices(search=f"INV-RC-SEARCH-{marker}", db=db_session)
    missing = list_invoices(search=f"not-present-{marker}", db=db_session)

    assert [invoice.invoice_number for invoice in by_parent] == [f"INV-RC-SEARCH-{marker}"]
    assert [invoice.invoice_number for invoice in by_number] == [f"INV-RC-SEARCH-{marker}"]
    assert missing == []
