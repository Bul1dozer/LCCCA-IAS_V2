from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVOICES_TEMPLATE = (ROOT / "templates" / "invoices.html").read_text()
COMMON_JS = (ROOT / "static" / "js" / "lcca-common.js").read_text()


def _between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_parent_invoice_mode_calls_parent_endpoint_without_learner_id():
    parent_branch = _between(
        INVOICES_TEMPLATE,
        'if (scope === "parent") {',
        "const learnerId = requireSelectedInt",
    )

    assert 'requireSelectedInt("genParent", "Parent")' in parent_branch
    assert "endpoint: `/parents/${parentId}/generate-invoice`" in parent_branch
    assert "payload: { parent_id: parentId, due_date: dueDate, billing_period: billingPeriod }" in parent_branch
    assert "learner_id" not in parent_branch


def test_learner_invoice_mode_requires_learner_and_calls_learner_endpoint():
    learner_branch = _between(
        INVOICES_TEMPLATE,
        'const learnerId = requireSelectedInt("genLearner", "Learner");',
        "function invoiceSuccessMessage",
    )

    assert 'const payload = { learner_id: learnerId, due_date: dueDate };' in learner_branch
    assert 'endpoint: "/invoices/generate"' in learner_branch
    assert "fee_structure_id" in learner_branch


def test_mode_switches_clear_irrelevant_form_state():
    scope_function = _between(
        INVOICES_TEMPLATE,
        "function setInvoiceScope(scope) {",
        "function requireSelectedInt",
    )

    assert 'document.getElementById("genLearner").value = "";' in scope_function
    assert 'document.getElementById("genFeeStructure").value = "";' in scope_function
    assert 'document.getElementById("genParent").value = "";' in scope_function


def test_submit_guard_prevents_duplicate_submissions_and_validates_before_post():
    submit_handler = _between(
        INVOICES_TEMPLATE,
        'document.getElementById("generateForm").addEventListener("submit", async (e) => {',
        "function openSendEmail",
    )

    assert "if (btn.disabled) return;" in submit_handler
    assert "requestConfig = buildGenerateInvoiceRequest();" in submit_handler
    assert "btn.disabled = true;" in submit_handler
    assert "LCCA.api.post(requestConfig.endpoint, requestConfig.payload)" in submit_handler


def test_successful_parent_generation_displays_combined_invoice_message():
    message_function = _between(
        INVOICES_TEMPLATE,
        "function invoiceSuccessMessage(invoice, scope) {",
        'document.getElementById("genScope").addEventListener',
    )

    assert 'scope === "parent"' in message_function
    assert "Combined invoice" in message_function
    assert "learner_count" in message_function


def test_api_validation_errors_are_formatted_for_people():
    assert "function formatApiErrorDetail(detail)" in COMMON_JS
    assert 'item.loc.filter((part) => part !== "body").join(".")' in COMMON_JS
    assert "JSON.stringify(data.detail)" not in COMMON_JS
    assert "formatApiErrorDetail" in COMMON_JS
