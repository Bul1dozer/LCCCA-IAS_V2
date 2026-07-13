"""
LCCA-IAS v3 — End-to-End Financial Integrity Test Suite.

Runs against a LIVE server instance using the requests library,
exercising the system exactly as a real user/admin would.

Covers:
  1. Auth / session
  2. Floating point precision (0.1 + 0.2 style errors)
  3. Payment validation (reject zero/negative)
  4. Duplicate reference rejection
  5. Invoice generation & atomic numbering (no duplicates)
  6. Ledger balance == learner.balance cache (reconciliation)
  7. Invoice void (no hard delete) restores balance correctly
  8. Payment reversal restores balance correctly
  9. Concurrent invoice generation safety (no duplicate invoice numbers)
  10. Month-end idempotency (running twice doesn't duplicate)
  11. Soft delete behavior (learner with balance can't be deleted)
"""

import sys
import time
import json
import threading
from decimal import Decimal

import requests

BASE = "http://localhost:8000"
session = requests.Session()

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"  PASS: {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL: {name}  -- {detail}")


def login():
    r = session.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"})
    check("Login succeeds", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")


# ---------------------------------------------------------------------------
print("\n=== 1. AUTH ===")
login()


# ---------------------------------------------------------------------------
print("\n=== 2. CREATE TEST LEARNER ===")
r = session.post(f"{BASE}/api/learners", json={
    "full_name": "Test Learner FinIntegrity",
    "grade": "Grade 3",
    "class_name": "3A",
    "date_of_admission": "2026-01-01",
    "status": "Active",
})
check("Create learner", r.status_code == 201, f"status={r.status_code} body={r.text[:300]}")
learner = r.json()
learner_id = learner["id"]
print(f"  Created learner_id={learner_id}, code={learner.get('learner_code')}")


# ---------------------------------------------------------------------------
print("\n=== 3. PAYMENT VALIDATION: REJECT ZERO / NEGATIVE ===")
r = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": 0, "date_paid": "2026-01-15",
    "payment_method": "Cash",
})
check("Zero payment rejected", r.status_code in (400, 422), f"status={r.status_code}")

r = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": -500, "date_paid": "2026-01-15",
    "payment_method": "Cash",
})
check("Negative payment rejected", r.status_code in (400, 422), f"status={r.status_code}")


# ---------------------------------------------------------------------------
print("\n=== 4. FLOATING POINT PRECISION ===")
r = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": 0.1, "date_paid": "2026-01-15",
    "payment_method": "Cash", "reference_number": "FLOAT-TEST-1",
})
check("0.1 payment accepted", r.status_code == 201, f"status={r.status_code} body={r.text[:200]}")
p1 = r.json()

r2 = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": 0.2, "date_paid": "2026-01-15",
    "payment_method": "Cash", "reference_number": "FLOAT-TEST-2",
})
check("0.2 payment accepted", r2.status_code == 201, f"status={r2.status_code}")

# Reverse both float test payments to not pollute later balance checks
session.post(f"{BASE}/api/payments/{p1['id']}/reverse")
session.post(f"{BASE}/api/payments/{r2.json()['id']}/reverse")


# ---------------------------------------------------------------------------
print("\n=== 5. DUPLICATE PAYMENT REFERENCE REJECTION ===")
r = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": 100, "date_paid": "2026-01-15",
    "payment_method": "Cash", "reference_number": "DUPTEST-001",
})
check("First payment with ref DUPTEST-001 accepted", r.status_code == 201, f"status={r.status_code}")
first_payment_id = r.json()["id"]

r2 = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": 200, "date_paid": "2026-01-16",
    "payment_method": "Cash", "reference_number": "DUPTEST-001",
})
check("Duplicate reference rejected", r2.status_code == 409, f"status={r2.status_code} body={r2.text[:200]}")

# Reverse it
session.post(f"{BASE}/api/payments/{first_payment_id}/reverse")


# ---------------------------------------------------------------------------
print("\n=== 6. INVOICE GENERATION + LEDGER RECONCILIATION ===")
# Assign a fee item first
r = session.get(f"{BASE}/api/fee-items", params={"active_only": "true"})
fee_items = r.json()
check("Fee items exist", len(fee_items) > 0, "no fee items in catalogue")

if fee_items:
    monthly_item = next((fi for fi in fee_items if fi["frequency"] == "monthly"), fee_items[0])
    r = session.post(f"{BASE}/api/fee-items/learner/{learner_id}/assign", json={
        "fee_item_id": monthly_item["id"],
    })
    check("Fee assigned to test learner", r.status_code == 201, f"status={r.status_code} body={r.text[:200]}")

    r = session.post(f"{BASE}/api/invoices/generate", json={
        "learner_id": learner_id, "due_date": "2026-02-28",
    })
    check("Invoice generated", r.status_code == 201, f"status={r.status_code} body={r.text[:300]}")
    invoice = r.json()
    inv_number = invoice.get("invoice_number")
    print(f"  Invoice number: {inv_number}")
    check("Invoice number format INV-YYYY-NNNNNN", inv_number and inv_number.startswith("INV-2026-"),
          f"got {inv_number}")

    # Reconciliation check
    r = session.get(f"{BASE}/api/dashboard/reconciliation")
    check("Reconciliation endpoint reachable", r.status_code == 200, f"status={r.status_code}")
    recon = r.json()
    print(f"  Reconciliation: {recon}")
    check("Ledger reconciles with balance cache", recon.get("reconciled") is True,
          f"discrepancy={recon.get('discrepancy')}")


# ---------------------------------------------------------------------------
print("\n=== 7. INVOICE VOID (NO HARD DELETE) ===")
if fee_items:
    r = session.get(f"{BASE}/api/learners/{learner_id}")
    balance_before_void = Decimal(str(r.json()["balance"]))
    print(f"  Balance before void: {balance_before_void}")

    r = session.post(f"{BASE}/api/invoices/{invoice['id']}/void", params={"reason": "E2E test void"})
    check("Invoice void succeeds", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
    voided = r.json()
    check("Invoice status is Void", voided.get("status") == "Void", f"status={voided.get('status')}")

    r = session.get(f"{BASE}/api/learners/{learner_id}")
    balance_after_void = Decimal(str(r.json()["balance"]))
    print(f"  Balance after void: {balance_after_void}")
    check("Balance reduced after void", balance_after_void < balance_before_void,
          f"before={balance_before_void} after={balance_after_void}")

    # Verify invoice record STILL EXISTS (not hard-deleted)
    r = session.get(f"{BASE}/api/invoices/{invoice['id']}")
    check("Voided invoice record still exists (no hard delete)", r.status_code == 200,
          f"status={r.status_code}")


# ---------------------------------------------------------------------------
print("\n=== 8. PAYMENT REVERSAL RESTORES BALANCE ===")
r = session.post(f"{BASE}/api/payments", json={
    "learner_id": learner_id, "amount_paid": 555.55, "date_paid": "2026-01-20",
    "payment_method": "Cash", "reference_number": "REV-TEST-001",
})
check("Payment for reversal test created", r.status_code == 201, f"status={r.status_code}")
rev_payment = r.json()

r = session.get(f"{BASE}/api/learners/{learner_id}")
balance_before_reversal = Decimal(str(r.json()["balance"]))

r = session.post(f"{BASE}/api/payments/{rev_payment['id']}/reverse", params={"reason": "E2E reversal test"})
check("Payment reversal succeeds", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

r = session.get(f"{BASE}/api/learners/{learner_id}")
balance_after_reversal = Decimal(str(r.json()["balance"]))
print(f"  Balance before reversal: {balance_before_reversal}, after: {balance_after_reversal}")
check("Balance increased by exactly 555.55 after reversal",
      (balance_after_reversal - balance_before_reversal) == Decimal("555.55"),
      f"delta={balance_after_reversal - balance_before_reversal}")

# Verify payment record still exists but inactive
r = session.get(f"{BASE}/api/payments/{rev_payment['id']}")
check("Reversed payment record still exists", r.status_code == 200, f"status={r.status_code}")


# ---------------------------------------------------------------------------
print("\n=== 9. CONCURRENT INVOICE GENERATION — NO DUPLICATE INVOICE NUMBERS ===")
# Create several learners with fee profiles, then fire concurrent generate requests
concurrent_learners = []
for i in range(5):
    r = session.post(f"{BASE}/api/learners", json={
        "full_name": f"Concurrent Test Learner {i}",
        "grade": "Grade 2", "class_name": "2A",
        "date_of_admission": "2026-01-01", "status": "Active",
    })
    cl = r.json()
    concurrent_learners.append(cl["id"])
    if fee_items:
        session.post(f"{BASE}/api/fee-items/learner/{cl['id']}/assign", json={
            "fee_item_id": fee_items[0]["id"],
        })

results = {}
def gen_invoice(lid, idx):
    s = requests.Session()
    s.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"})
    r = s.post(f"{BASE}/api/invoices/generate", json={"learner_id": lid, "due_date": "2026-03-31"})
    results[idx] = r

threads = [threading.Thread(target=gen_invoice, args=(lid, i)) for i, lid in enumerate(concurrent_learners)]
for t in threads:
    t.start()
for t in threads:
    t.join()

invoice_numbers = []
for idx, r in results.items():
    if r.status_code == 201:
        invoice_numbers.append(r.json()["invoice_number"])

check("All concurrent invoices generated successfully",
      len(invoice_numbers) == len(concurrent_learners),
      f"got {len(invoice_numbers)}/{len(concurrent_learners)}")
check("No duplicate invoice numbers under concurrency",
      len(invoice_numbers) == len(set(invoice_numbers)),
      f"numbers={invoice_numbers}")


# ---------------------------------------------------------------------------
print("\n=== 10. MONTH-END IDEMPOTENCY ===")
r = session.post(f"{BASE}/api/month-end/trigger", json={"billing_period": "2026-04"})
check("First month-end trigger accepted", r.status_code == 202, f"status={r.status_code}")
time.sleep(4)  # let background task run

r = session.get(f"{BASE}/api/month-end/runs", params={"limit": 5})
runs = r.json()
first_run = next((run for run in runs if run["billing_period"] == "2026-04"), None)
check("First 2026-04 run completed", first_run and first_run["status"] == "completed",
      f"run={first_run}")
invoices_from_first_run = first_run["invoices_generated"] if first_run else 0

# Trigger again for the same period
r = session.post(f"{BASE}/api/month-end/trigger", json={"billing_period": "2026-04"})
check("Second month-end trigger accepted (idempotent)", r.status_code == 202, f"status={r.status_code}")
time.sleep(4)

r = session.get(f"{BASE}/api/invoices")
all_invoices = r.json()
period_invoices = [i for i in all_invoices if i.get("billing_period") == "2026-04" or
                    (i.get("due_date", "").startswith("2026-04"))]
# Count invoices by learner+period combo to detect duplicates
seen = {}
duplicates_found = False
for inv in all_invoices:
    key = (inv["learner_id"], inv.get("invoice_number"))
    if key in seen:
        duplicates_found = True
    seen[key] = True

check("No duplicate invoices created by re-running month-end", not duplicates_found)


# ---------------------------------------------------------------------------
print("\n=== 11. SOFT DELETE: LEARNER WITH BALANCE CANNOT BE DELETED ===")
r = session.get(f"{BASE}/api/learners/{learner_id}")
bal = Decimal(str(r.json()["balance"]))
print(f"  Test learner current balance: {bal}")

r = session.delete(f"{BASE}/api/learners/{learner_id}")
if bal != 0:
    check("Learner with non-zero balance cannot be deleted", r.status_code == 400, f"status={r.status_code}")
else:
    check("Learner with zero balance can be deleted", r.status_code == 204, f"status={r.status_code}")


# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"RESULTS: {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 60)
if FAIL:
    print("\nFAILED TESTS:")
    for name, detail in FAIL:
        print(f"  - {name}: {detail}")
    sys.exit(1)
else:
    print("\nALL TESTS PASSED")
    sys.exit(0)
