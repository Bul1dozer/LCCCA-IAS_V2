"""Seed initial data: admin user, roles, fee catalogue, sample learners/parents."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from . import models, auth
from .ledger import post_invoice, post_payment, next_invoice_number, quantize


def seed(db: Session):
    if db.query(models.User).count() > 0:
        return

    # Admin user
    admin = models.User(
        username="admin",
        password_hash=auth.hash_password("admin123"),
        full_name="System Administrator",
        email="admin@lcca.na",
        role="Administrator",
        is_active=True,
    )
    db.add(admin)

    # Invoice counter
    year = datetime.now().year
    if not db.query(models.InvoiceCounter).first():
        db.add(models.InvoiceCounter(id=1, year=year, last_sequence=0))

    # Fee Catalogue - 2027 published LCCCA Academy fees.
    fee_items = [
        ("Registration Fee - Babies Academy", "registration", "annual", 500.00, True, "Baby Class"),
        ("Tuition Fee - Babies Academy", "tuition", "monthly", 1838.00, True, "Baby Class"),
        ("Registration Fee - Toddler Academy", "registration", "annual", 500.00, True, "Toddler Class"),
        ("Tuition Fee - Toddler Academy", "tuition", "monthly", 1475.00, True, "Toddler Class"),
        ("Registration Fee - Grade 0-7", "registration", "annual", 850.00, True, "Grade 0,Grade 1,Grade 2,Grade 3,Grade 4,Grade 5,Grade 6,Grade 7"),
        ("Tuition Fee - Grade 0-7", "tuition", "monthly", 1838.00, True, "Grade 0,Grade 1,Grade 2,Grade 3,Grade 4,Grade 5,Grade 6,Grade 7"),
        ("Sports & Culture - Grade 0-7", "sport", "monthly", 50.00, True, "Grade 0,Grade 1,Grade 2,Grade 3,Grade 4,Grade 5,Grade 6,Grade 7"),
        ("Registration Fee - Grade 8-10", "registration", "annual", 1580.00, True, "Grade 8,Grade 9,Grade 10"),
        ("Tuition Fee - Grade 8-10", "tuition", "monthly", 2650.00, True, "Grade 8,Grade 9,Grade 10"),
        ("Sports & Culture - Grade 8-10", "sport", "monthly", 100.00, True, "Grade 8,Grade 9,Grade 10"),
        ("After School Care - Internal Learners", "aftercare", "monthly", 550.00, False, None),
        ("After School Care Registration - External Learners", "registration", "annual", 450.00, False, None),
        ("After School Care - External Learners", "aftercare", "monthly", 1035.00, False, None),
        ("Bus Fee - Standard Monthly Rate", "transport", "monthly", 1155.00, False, None),
        ("Application Form", "other", "once_off", 50.00, False, None),
    ]
    created_fi = []
    for i, (name, cat, freq, amt, mandatory, grades) in enumerate(fee_items):
        fi = models.FeeItem(
            name=name, category=cat, frequency=freq,
            amount=Decimal(str(amt)), is_mandatory=mandatory,
            is_active=True, sort_order=i,
            applicable_grades=grades,
        )
        db.add(fi)
        created_fi.append(fi)
    db.flush()

    db.add(models.TransportRoute(
        name="Standard Bus Route",
        description="Published standard route price. Fee may increase for learners living farther from the Academy.",
        monthly_fee=Decimal("1155.00"),
        is_active=True,
    ))

    def make_code(db_session, adm_date):
        prefix = adm_date.strftime("%Y%m%d")
        cnt = db_session.query(models.Learner).filter(
            models.Learner.learner_code.like(f"{prefix}%")
        ).count()
        return f"{prefix}{(cnt+1):02d}"

    # Sample learners
    learners_raw = [
        ("Tendai Mukasa",        "Grade 5",  "5A",  date(2021, 1, 10), "Active",   "12 Church St, Windhoek"),
        ("Naledi Shilongo",      "Grade 7",  "7B",  date(2019, 1, 8),  "Active",   "45 Sam Nujoma Ave, Windhoek"),
        ("Joshua Hamutenya",     "Grade 9",  "9A",  date(2017, 1, 12), "Active",   "8 Pioneer St, Windhoek"),
        ("Grace Nangolo",        "Grade 3",  "3A",  date(2023, 1, 9),  "Active",   "22 Robert Mugabe Ave, Windhoek"),
        ("Daniel Kapere",        "Grade 1",  "1B",  date(2025, 1, 7),  "Active",   "5 Lüderitz St, Windhoek"),
        ("Amara Tjikuua",        "Grade 10", "10A", date(2016, 1, 11), "Active",   "33 Hosea Kutako Blvd, Windhoek"),
        ("Miriam Nghifindaka",   "Baby Class","BC1", date(2026, 1, 6),  "Active",   "7 Mandume Rd, Windhoek"),
        ("Elias Katjivena",      "Grade 0",  "G0A", date(2026, 1, 6),  "Active",   "19 Freedom Ave, Windhoek"),
        ("Sophia Haikali",       "Grade 8",  "8B",  date(2018, 1, 13), "Active",   "3 Werner List Rd, Windhoek"),
        ("Samuel Tjivikua",      "Baby Class","BC1", date(2025, 1, 13), "Active",   "14 Stübel St, Windhoek"),
        ("Ruth Katjivena",       "Grade 4",  "4A",  date(2021, 1, 11), "Inactive", "6 Bismarck St, Windhoek"),
        ("Peter Shikongo",       "Grade 8",  "8A",  date(2019, 1, 9),  "Active",   "29 Jan Jonker Rd, Windhoek"),
    ]
    learners = []
    for name, grade, cls, adm, status, addr in learners_raw:
        code = make_code(db, adm)
        l = models.Learner(
            learner_code=code, full_name=name, grade=grade,
            class_name=cls, date_of_admission=adm, status=status,
            balance=Decimal("0.00"), physical_address=addr,
        )
        db.add(l)
        db.flush()

        # Auto-assign all mandatory catalogue fees that apply to this learner's grade.
        for fi in created_fi:
            if not fi.is_mandatory:
                continue
            grades = [g.strip() for g in fi.applicable_grades.split(",")] if fi.applicable_grades else []
            if grades and grade not in grades:
                continue
            db.add(models.LearnerFeeItem(
                learner_id=l.id, fee_item_id=fi.id,
                is_active=True, assigned_by="seed",
            ))
        learners.append(l)

    db.flush()

    # Sample parents with employer details
    parents_raw = [
        ("Anna Mukasa",     "anna@email.na",      "+264811001001", "Ministry of Education", "Government Enclave",      "Head of Department"),
        ("John Shilongo",   "john@email.na",       "+264812002002", "Bank Windhoek",          "275 Independence Ave",   "Senior Manager"),
        ("Maria Hamutenya", "maria@email.na",      "+264813003003", "Namibia Power Corp",     "15 Luther St",            "Engineer"),
        ("David Nangolo",   "david@email.na",      "+264814004004", "Spar Namibia",           "46 Sam Nujoma Ave",       "Store Manager"),
        ("Petrus Kapere",   "petrus@email.na",     "+264815005005", "NamWater",               "Corner Uhland/Garten",    "Technician"),
        ("Helena Tjikuua",  "helena@email.na",     "+264816006006", "Standard Bank Namibia",  "Town Square",             "Branch Manager"),
    ]
    parents = []
    for fname, email, phone, emp_name, emp_addr, occ in parents_raw:
        p = models.Parent(
            full_name=fname, email=email, phone=phone,
            employer_name=emp_name, employer_address=emp_addr,
            occupation=occ, is_active=True,
        )
        db.add(p)
        parents.append(p)
    db.flush()

    # Link learners to parents
    links = [(0,0,"Mother"),(1,1,"Father"),(2,2,"Mother"),(3,3,"Father"),
             (4,4,"Father"),(5,5,"Mother"),(6,0,"Mother"),(7,1,"Father"),
             (8,2,"Mother"),(9,3,"Father"),(10,4,"Father"),(11,5,"Mother")]
    for li, pi, rel_type in links:
        db.add(models.LearnerParentRelationship(
            learner_id=learners[li].id, parent_id=parents[pi].id,
            relationship_type=rel_type, is_primary=True,
        ))

    db.flush()

    # Generate seed invoices for active learners
    issue_date = date(2026, 1, 1)
    due_date = date(2026, 1, 31)
    for learner in learners[:10]:
        if learner.status != "Active":
            continue
        assignments = db.query(models.LearnerFeeItem).join(models.FeeItem).filter(
            models.LearnerFeeItem.learner_id == learner.id,
            models.LearnerFeeItem.is_active == True,
            models.FeeItem.frequency == "monthly",
        ).all()
        if not assignments:
            continue
        charges = quantize(sum(
            Decimal(str(a.custom_amount if a.custom_amount else a.fee_item.amount))
            for a in assignments
        ))
        inv_num = next_invoice_number(db)
        inv = models.Invoice(
            invoice_number=inv_num, learner_id=learner.id,
            issue_date=issue_date, due_date=due_date,
            previous_balance=Decimal("0.00"), current_charges=charges,
            payments_made=Decimal("0.00"), outstanding_balance=charges,
            status="Generated", billing_period="2026-01", created_by="seed",
        )
        db.add(inv)
        db.flush()
        for a in assignments:
            db.add(models.InvoiceItem(
                invoice_id=inv.id, learner_id=learner.id,
                description=a.fee_item.name, amount=a.custom_amount or a.fee_item.amount,
            ))
        post_invoice(db, learner.id, inv.id, charges, issue_date, "seed")

    db.flush()

    # Sample payments
    payments_raw = [
        (0, 900.00,  5,  "EFT",  "EFT-2026-001"),
        (1, 1000.00, 7,  "Cash", "RCP-2026-001"),
        (2, 750.00,  3,  "EFT",  "EFT-2026-002"),
        (3, 500.00,  10, "Card", "CRD-2026-001"),
        (4, 400.00,  4,  "EFT",  "EFT-2026-003"),
        (5, 2000.00, 6,  "EFT",  "EFT-2026-004"),
        (6, 300.00,  8,  "Cash", "RCP-2026-002"),
        (7, 600.00,  9,  "EFT",  "EFT-2026-005"),
        (8, 800.00,  5,  "Card", "CRD-2026-002"),
        (9, 450.00,  7,  "Cash", "RCP-2026-003"),
        (0, 400.00,  12, "EFT",  "EFT-2026-006"),
    ]
    for li, amount, days, method, ref in payments_raw:
        learner = learners[li]
        pay_date = issue_date + timedelta(days=days)
        p = models.Payment(
            learner_id=learner.id, amount_paid=Decimal(str(amount)),
            payment_date=pay_date, date_paid=pay_date,
            payment_method=method, reference_number=ref,
            created_by="seed", is_active=True,
        )
        db.add(p)
        db.flush()
        post_payment(db, learner.id, p.id, Decimal(str(amount)), pay_date, "seed")

    db.commit()
    print("✓ Seed data loaded")
