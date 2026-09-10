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

    assert 'const payload = { learner_id: learnerId, due_date: dueDate, billing_period: billingPeriod };' in learner_branch
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


def test_invoice_table_is_parent_centred_with_linked_learners_column():
    assert "<th>Invoice Number</th>" in INVOICES_TEMPLATE
    assert "<th>Parent</th>" in INVOICES_TEMPLATE
    assert "<th>Linked Learners</th>" in INVOICES_TEMPLATE
    assert "<th>Billing Period</th>" in INVOICES_TEMPLATE
    assert "<th>Current Charges</th>" in INVOICES_TEMPLATE
    assert "<th>Previous Balance</th>" in INVOICES_TEMPLATE
    assert "<th>Payments</th>" in INVOICES_TEMPLATE
    assert "<th>Outstanding Balance</th>" in INVOICES_TEMPLATE
    assert "<th>Learner</th>" not in _between(
        INVOICES_TEMPLATE,
        "<table class=\"table-lcca\">",
        "</thead>",
    )


def test_invoice_table_expands_learner_specific_line_items():
    assert "function toggleInvoiceItems(invoiceId)" in INVOICES_TEMPLATE
    assert "function renderInvoiceItemRow(inv)" in INVOICES_TEMPLATE
    assert "<th>Learner</th>" in _between(
        INVOICES_TEMPLATE,
        "function renderInvoiceItemRow(inv)",
        "function toggleInvoiceItems",
    )
    assert "item.learner_name" in INVOICES_TEMPLATE
    assert "item.description" in INVOICES_TEMPLATE
    assert "item.amount" in INVOICES_TEMPLATE


def test_invoice_search_targets_parent_name_and_invoice_number():
    search_filter = _between(
        INVOICES_TEMPLATE,
        "if (search) {",
        "if (status)",
    )

    assert "(i.parent_name || \"\").toLowerCase().includes(search)" in search_filter
    assert "(i.invoice_number || \"\").toLowerCase().includes(search)" in search_filter
    assert "(i.learner_name || \"\").toLowerCase().includes(search)" not in search_filter
    assert "Search by parent name or invoice number" in INVOICES_TEMPLATE


def test_invoice_table_uses_parent_and_legacy_fallback_display_helpers():
    assert "function billingPartyDisplay(inv)" in INVOICES_TEMPLATE
    assert 'return inv.parent_name || "Legacy learner invoice";' in INVOICES_TEMPLATE
    assert "function linkedLearnerDisplay(inv)" in INVOICES_TEMPLATE
    assert "inv.linked_learner_names.join" in INVOICES_TEMPLATE
    assert "inv.learner_name || \"-\"" in INVOICES_TEMPLATE


def test_invoice_table_uses_signed_balance_state_helper():
    assert "function balanceCell(balance)" in INVOICES_TEMPLATE
    assert "LCCA.balanceDisplay(balance)" in INVOICES_TEMPLATE
    assert "balanceCell(inv.outstanding_balance)" in INVOICES_TEMPLATE
    assert "Credit carried forward" in COMMON_JS
    assert "Do not pay - credit applied" in COMMON_JS


def test_api_validation_errors_are_formatted_for_people():
    assert "function formatApiErrorDetail(detail)" in COMMON_JS
    assert 'item.loc.filter((part) => part !== "body").join(".")' in COMMON_JS
    assert "JSON.stringify(data.detail)" not in COMMON_JS
    assert "formatApiErrorDetail" in COMMON_JS
