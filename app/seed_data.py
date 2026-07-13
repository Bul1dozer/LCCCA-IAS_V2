"""
LCCA-IAS v2 seed data.
Preserves all v1 seeding + adds:
  - Dynamic FeeItems (Fee Catalogue)
  - TransportRoutes
  - Per-learner fee profiles (LearnerFeeItems)
  - v2-style invoices generated from the fee engine
  - RBAC roles
  - Email templates
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from . import models, auth
from .fee_engine import auto_assign_mandatory_fees, generate_invoice_for_learner
from .ledger import post_payment


def seed_if_empty(db: Session):
    if db.query(models.User).count() > 0:
        return

    # ------------------------------------------------------------------ Users
    admin = models.User(
        username="admin",
        password_hash=auth.hash_password("admin123"),
        full_name="School Administrator",
        email="admin@lcca.edu.na",
        role="Administrator",
        is_active=True,
    )
    db.add(admin)

    # ------------------------------------------------------------------ Roles
    for role_name, perms in [
        ("Administrator", '["*"]'),
        ("Accountant", '["payments","invoices","reports","learners:read","parents:read"]'),
        ("Principal", '["reports:read","dashboard:read","learners:read"]'),
        ("Parent", '["own_invoices:read","own_payments:read","own_statements:read"]'),
    ]:
        db.add(models.Role(name=role_name, permissions=perms))

    # ------------------------------------------------------------------ Email templates
    db.add(models.EmailTemplate(
        name="invoice_notification",
        subject_template="LCCA Invoice {{invoice_number}} — {{learner_name}}",
        body_html_template="""<p>Dear {{parent_name}},</p>
<p>Please find attached Invoice <strong>{{invoice_number}}</strong> for {{learner_name}} ({{learner_code}}).</p>
<p>Outstanding balance: <strong>N$ {{outstanding_balance}}</strong><br>
Due date: <strong>{{due_date}}</strong></p>
<p>Please use Learner ID <strong>{{learner_code}}</strong> as your payment reference.</p>
<p>Kind regards,<br>LCCA Accounts Office</p>""",
        body_text_template="Invoice {{invoice_number}} for {{learner_name}}. Balance: N$ {{outstanding_balance}}. Due: {{due_date}}.",
    ))

    # ------------------------------------------------------------------ Transport routes
    routes = [
        models.TransportRoute(name="Route A — Katutura", description="Katutura North/South", monthly_fee=600),
        models.TransportRoute(name="Route B — Khomasdal", description="Khomasdal & Hochland Park", monthly_fee=550),
        models.TransportRoute(name="Route C — Olympia", description="Olympia & Pioneers Park", monthly_fee=700),
    ]
    for r in routes:
        db.add(r)
    db.flush()
    route_a, route_b, route_c = routes

    # ------------------------------------------------------------------ Fee Catalogue
    fee_items_data = [
        # Mandatory — all grades
        dict(name="Tuition Fee", category="tuition", frequency="monthly", amount=3500,
             is_mandatory=True, sort_order=1, description="Monthly tuition for Baby–Grade 3",
             applicable_grades="Baby Class,Toddler Class,Grade 0,Grade 1,Grade 2,Grade 3"),
        dict(name="Tuition Fee", category="tuition", frequency="monthly", amount=4200,
             is_mandatory=True, sort_order=1, description="Monthly tuition for Grade 4–7",
             applicable_grades="Grade 4,Grade 5,Grade 6,Grade 7"),
        dict(name="Tuition Fee", category="tuition", frequency="monthly", amount=5200,
             is_mandatory=True, sort_order=1, description="Monthly tuition for Grade 8–9",
             applicable_grades="Grade 8,Grade 9"),
        dict(name="Development Fee", category="material", frequency="monthly", amount=500,
             is_mandatory=True, sort_order=2),
        dict(name="Cleaning Materials", category="material", frequency="monthly", amount=150,
             is_mandatory=True, sort_order=3),
        # Annual mandatory
        dict(name="Registration Fee", category="registration", frequency="annual", amount=1200,
             is_mandatory=True, sort_order=0),
        # Optional extras
        dict(name="Aftercare", category="aftercare", frequency="monthly", amount=800,
             is_mandatory=False, sort_order=10, description="After-school care until 17:30"),
        dict(name="Sports Programme", category="sport", frequency="monthly", amount=350,
             is_mandatory=False, sort_order=11),
        dict(name="Ballet", category="activity", frequency="monthly", amount=450,
             is_mandatory=False, sort_order=12),
        dict(name="Robotics Club", category="activity", frequency="monthly", amount=500,
             is_mandatory=False, sort_order=13),
        dict(name="Reading Books (Term)", category="material", frequency="term", amount=280,
             is_mandatory=False, sort_order=14),
        dict(name="Transport — Route A", category="transport", frequency="monthly", amount=600,
             is_mandatory=False, sort_order=20),
        dict(name="Transport — Route B", category="transport", frequency="monthly", amount=550,
             is_mandatory=False, sort_order=21),
        dict(name="Transport — Route C", category="transport", frequency="monthly", amount=700,
             is_mandatory=False, sort_order=22),
    ]
    fee_items = []
    for fd in fee_items_data:
        fi = models.FeeItem(
            name=fd["name"], description=fd.get("description"),
            category=fd["category"], frequency=fd["frequency"],
            amount=fd["amount"], is_mandatory=fd["is_mandatory"],
            is_active=True, sort_order=fd.get("sort_order", 0),
            applicable_grades=fd.get("applicable_grades"),
        )
        db.add(fi)
        fee_items.append(fi)
    db.flush()

    # Index fee items by name for easy reference
    fi_idx = {f"{fi.name}|{fi.category}": fi for fi in fee_items}
    def fi(name, cat=None):
        for k, v in fi_idx.items():
            if k.startswith(name):
                if cat is None or f"|{cat}" in k:
                    return v
        return None

    tuition_jr = fi("Tuition Fee|tuition")  # first one = Baby-Grade 3
    # We'll use the grade-appropriate tuition per learner
    dev_fee = fi("Development Fee")
    cleaning = fi("Cleaning Materials")
    reg_fee = fi("Registration Fee")
    aftercare_fi = fi("Aftercare")
    sports_fi = fi("Sports Programme")
    ballet_fi = fi("Ballet")
    robotics_fi = fi("Robotics Club")
    reading_fi = fi("Reading Books (Term)")
    transport_a = fi("Transport — Route A")
    transport_b = fi("Transport — Route B")

    # ------------------------------------------------------------------ Learners
    learners_raw = [
        ("Tendai Mukasa",    "Grade 2",  "2A", date(2024, 1, 15), "Active"),
        ("Naledi Shilongo",  "Grade 3",  "3B", date(2023, 1, 20), "Active"),
        ("Joshua Hamutenya", "Grade 5",  "5A", date(2022, 1, 10), "Active"),
        ("Grace Nangolo",    "Grade 6",  "6B", date(2021, 1, 18), "Active"),
        ("Daniel Kapere",    "Grade 7",  "7A", date(2020, 1, 25), "Active"),
        ("Faith Amukoshi",   "Grade 9",  "9A", date(2019, 1, 14), "Active"),
        ("Michael Iyambo",   "Grade 8",  "8A", date(2018, 1, 22), "Active"),  # idx 6
        ("Esther Nakale",    "Grade 9",  "9A", date(2017, 1, 16), "Active"),
        ("Samuel Tjivikua",  "Baby Class","BC1",date(2025, 1, 13), "Active"),
        ("Ruth Katjivena",   "Grade 4",  "4A", date(2021, 1, 11), "Inactive"),
        ("Peter Shikongo",   "Grade 8",  "8A", date(2019, 1,  9), "Active"),
        ("Lydia Hango",      "Grade 9",  "9A", date(2016, 1, 12), "Graduated"),
    ]
    learners = []
    for idx, (name, grade, cls, admission, status) in enumerate(learners_raw, start=1):
        l = models.Learner(
            learner_code=f"LCCA-2026-{idx:04d}", full_name=name, grade=grade,
            class_name=cls, date_of_admission=admission, status=status, balance=0.0,
        )
        db.add(l)
        learners.append(l)
    db.flush()

    # ------------------------------------------------------------------ Parents
    parents_raw = [
        ("Mr. & Mrs. Mukasa",     "mukasa.family@example.com",   "+264 81 234 5601", "15 Olive St",      "Father", [0]),
        ("Mrs. Helen Shilongo",   "h.shilongo@example.com",      "+264 81 234 5602", "22 Acacia Rd",     "Mother", [1]),
        ("Mr. James Hamutenya",   "j.hamutenya@example.com",     "+264 81 234 5603", "7 Independence Ave","Father", [2, 3]),
        ("Mrs. Anna Kapere",      "a.kapere@example.com",        "+264 81 234 5604", "31 Mandume St",    "Mother", [4]),
        ("Mr. Thomas Amukoshi",   "t.amukoshi@example.com",      "+264 81 234 5605", "9 Garden Rd",      "Father", [5]),
        ("Mrs. Sarah Iyambo",     "s.iyambo@example.com",        "+264 81 234 5606", "44 Sam Nujoma Dr", "Mother", [6]),
        ("Mr. & Mrs. Nakale",     "nakale.family@example.com",   "+264 81 234 5607", "18 Robert Mugabe", "Guardian",[7]),
        ("Mr. David Tjivikua",    "d.tjivikua@example.com",      "+264 81 234 5608", "2 Von Francois St","Father", [8]),
        ("Mrs. Monica Katjivena", "m.katjivena@example.com",     "+264 81 234 5609", "5 Hosea Kutako Dr","Mother", [9]),
        ("Mr. Paul Shikongo",     "p.shikongo@example.com",      "+264 81 234 5610", "11 Fidel Castro St","Father",[10]),
        ("Mrs. Ndapanda Hango",   "n.hango@example.com",         "+264 81 234 5611", "27 Centaurus Rd",  "Mother", [11]),
    ]
    for fn, em, ph, addr, rel, idxs in parents_raw:
        p = models.Parent(full_name=fn, email=em, phone=ph, address=addr)
        db.add(p)
        db.flush()
        for i in idxs:
            db.add(models.LearnerParentRelationship(
                learner_id=learners[i].id, parent_id=p.id, relationship_type=rel
            ))

    db.flush()

    # ------------------------------------------------------------------ Assign fee profiles
    # Pick grade-appropriate tuition item by matching applicable_grades
    def get_tuition(grade: str):
        for fi_obj in fee_items:
            if fi_obj.category == "tuition" and fi_obj.applicable_grades:
                if grade in fi_obj.applicable_grades:
                    return fi_obj
        return fee_items[0]  # fallback

    def assign(learner, fi_obj, custom=None, notes=None):
        if fi_obj is None:
            return
        db.add(models.LearnerFeeItem(
            learner_id=learner.id, fee_item_id=fi_obj.id,
            custom_amount=custom, is_active=True,
            assigned_by="seed", notes=notes,
        ))

    # Learner 0 — Tendai: Tuition + Dev + Cleaning + Sports + Transport A
    l = learners[0]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, sports_fi); assign(l, transport_a)

    # Learner 1 — Naledi: Tuition + Dev + Cleaning + Aftercare + Ballet + Transport B
    l = learners[1]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, aftercare_fi); assign(l, ballet_fi); assign(l, transport_b)

    # Learner 2 — Joshua: Tuition + Dev + Cleaning + Robotics + Sports
    l = learners[2]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, robotics_fi); assign(l, sports_fi)

    # Learner 3 — Grace: Tuition + Dev + Cleaning + Aftercare + Reading
    l = learners[3]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, aftercare_fi); assign(l, reading_fi)

    # Learner 4 — Daniel: Tuition + Dev + Cleaning only
    l = learners[4]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee)

    # Learner 5 — Faith: Senior Tuition + Dev + Cleaning + Sports + Ballet
    l = learners[5]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, sports_fi); assign(l, ballet_fi)

    # Learner 6 — Michael: Senior + Dev + Cleaning + Robotics + Transport A
    l = learners[6]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, robotics_fi); assign(l, transport_a)

    # Learner 7 — Esther: Senior + Dev + Cleaning + Aftercare
    l = learners[7]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, aftercare_fi)

    # Learner 8 — Samuel (Baby): Tuition + Dev + Cleaning + Transport C (custom rate)
    l = learners[8]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee)
    # Custom transport override for rural area
    t_c = fi("Transport — Route C")
    assign(l, t_c, custom=750, notes="Extended route — custom rate")

    # Learner 9 — Ruth (Inactive): basic profile, won't be billed
    l = learners[9]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee)

    # Learner 10 — Peter: Senior + Dev + Cleaning + Sports
    l = learners[10]
    assign(l, get_tuition(l.grade)); assign(l, dev_fee); assign(l, cleaning)
    assign(l, reg_fee); assign(l, sports_fi)

    # Learner 11 — Lydia (Graduated): no new fees assigned

    db.flush()

    # ------------------------------------------------------------------ v1 Fee Structures (legacy compat)
    fee_structures = [
        models.FeeStructure(name="Term 1 2026", grade="Grade 1-3", term="Term 1 2026",
            tuition_fee=3500, development_fee=500, hostel_fee=0, transport_fee=600, misc_charges=150,
            total=4750, is_legacy=True),
        models.FeeStructure(name="Term 1 2026", grade="Grade 4-7", term="Term 1 2026",
            tuition_fee=4200, development_fee=600, hostel_fee=0, transport_fee=600, misc_charges=200,
            total=5600, is_legacy=True),
        models.FeeStructure(name="Term 1 2026 (Boarding)", grade="Grade 8-9", term="Term 1 2026",
            tuition_fee=5200, development_fee=800, hostel_fee=2500, transport_fee=0, misc_charges=250,
            total=8750, is_legacy=True),
    ]
    for fs in fee_structures:
        db.add(fs)
    db.flush()

    # ------------------------------------------------------------------ Invoices via fee engine
    issue_date = date(2026, 1, 20)
    due_date = date(2026, 2, 28)
    billing_period = "2026-01"

    generated_invoices = []
    for learner in learners:
        if learner.status in ("Graduated", "Inactive"):
            continue
        invoice = generate_invoice_for_learner(
            db=db, learner=learner, due_date=due_date,
            billing_period=billing_period, frequency="monthly",
        )
        if invoice:
            invoice.created_at = datetime(2026, 1, 20, 8, 0, 0) + timedelta(minutes=len(generated_invoices))
            invoice.issue_date = issue_date
            generated_invoices.append(invoice)
    db.flush()

    # ------------------------------------------------------------------ Payments
    payments_raw = [
        (0, 2000, 5,  "Bank Transfer", "FNB-22910"),
        (0, 2200, 20, "Cash",          "RCPT-1001"),
        (1, 4000, 3,  "EFT",           "BWN-77231"),
        (2, 3500, 10, "Mobile Money",  "MTC-902331"),
        (3, 3000, 25, "Bank Transfer", "FNB-22987"),
        (4, 2000, 7,  "Cash",          "RCPT-1002"),
        (5, 6000, 4,  "Bank Transfer", "FNB-23004"),
        (6, 4000, 12, "EFT",           "BWN-77390"),
        (7, 5200, 8,  "Card",          "VISA-55821"),
        (8, 3000, 14, "Cash",          "RCPT-1003"),
        (10, 3500, 6, "Mobile Money",  "MTC-902450"),
    ]
    for li, amount, days_after, method, ref in payments_raw:
        l = learners[li]
        pay_date = issue_date + timedelta(days=days_after)
        p = models.Payment(
            learner_id=l.id, amount_paid=amount, payment_date=pay_date, date_paid=pay_date,
            payment_method=method, reference_number=ref, created_by="seed",
            created_at=datetime(pay_date.year, pay_date.month, pay_date.day, 9, 0),
        )
        db.add(p)
        db.flush()
        post_payment(
            db=db, learner_id=l.id, payment_id=p.id, amount=Decimal(str(amount)),
            payment_date=pay_date, created_by="seed", notes=f"Seed payment {ref}",
        )

    db.flush()

    # Mark a few invoices as Sent and add email logs
    for learner in learners[:3]:
        inv = db.query(models.Invoice).filter(models.Invoice.learner_id == learner.id).first()
        if inv:
            inv.status = "Sent"
            db.add(models.EmailLog(
                invoice_id=inv.id, recipient_email="parent@example.com",
                subject=f"LCCA Invoice {inv.invoice_number} — {learner.full_name}",
                body_preview=f"Invoice {inv.invoice_number} for {learner.full_name}. "
                             f"Balance: N$ {learner.balance:,.2f}. Due: {due_date:%d %b %Y}.",
                status="Simulated", sent_at=datetime(2026, 1, 21, 10, 0),
            ))

    db.commit()
