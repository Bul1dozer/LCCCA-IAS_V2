from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_invoice_pdf_uses_current_school_contact_details():
    pdf_generator = (ROOT / "app" / "pdf_generator.py").read_text()

    assert "Invokavit Str, Gemeente 1, Katutura, Windhoek, Namibia" in pdf_generator
    assert "Corner of Dawid Goraseb" not in pdf_generator
    assert "info@lcccacademy.com" in pdf_generator
    assert "081 749 6077 / 085 551 5495" in pdf_generator


def test_seed_fee_catalogue_matches_2027_fee_form():
    seed_data = (ROOT / "app" / "seed_data.py").read_text()
    expected_fee_rows = [
        '("Registration Fee - Babies Academy", "registration", "annual", 500.00',
        '("Tuition Fee - Babies Academy", "tuition", "monthly", 1838.00',
        '("Registration Fee - Toddler Academy", "registration", "annual", 500.00',
        '("Tuition Fee - Toddler Academy", "tuition", "monthly", 1475.00',
        '("Registration Fee - Grade 0-7", "registration", "annual", 850.00',
        '("Tuition Fee - Grade 0-7", "tuition", "monthly", 1838.00',
        '("Sports & Culture - Grade 0-7", "sport", "monthly", 50.00',
        '("Registration Fee - Grade 8-10", "registration", "annual", 1580.00',
        '("Tuition Fee - Grade 8-10", "tuition", "monthly", 2650.00',
        '("Sports & Culture - Grade 8-10", "sport", "monthly", 100.00',
        '("After School Care - Internal Learners", "aftercare", "monthly", 550.00',
        '("After School Care Registration - External Learners", "registration", "annual", 450.00',
        '("After School Care - External Learners", "aftercare", "monthly", 1035.00',
        '("Bus Fee - Standard Monthly Rate", "transport", "monthly", 1155.00',
        '("Application Form", "other", "once_off", 50.00',
    ]

    for row in expected_fee_rows:
        assert row in seed_data


def test_learner_invoice_generation_persists_selected_billing_period():
    schemas = (ROOT / "app" / "schemas.py").read_text()
    invoice_router = (ROOT / "app" / "routers" / "invoices.py").read_text()

    assert "billing_period: Optional[str] = None" in schemas
    assert 'billing_period = payload.billing_period or payload.due_date.strftime("%Y-%m")' in invoice_router
    assert "billing_period=billing_period" in invoice_router
