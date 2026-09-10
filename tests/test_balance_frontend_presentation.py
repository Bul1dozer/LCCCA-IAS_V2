from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = (ROOT / "templates" / "dashboard.html").read_text()
LEARNERS_TEMPLATE = (ROOT / "templates" / "learners.html").read_text()
COMMON_JS = (ROOT / "static" / "js" / "lcca-common.js").read_text()


def test_shared_balance_display_handles_credit_debit_and_settled_states():
    assert "function balanceDisplay(value)" in COMMON_JS
    assert 'state: "debit"' in COMMON_JS
    assert 'state: "credit"' in COMMON_JS
    assert 'state: "settled"' in COMMON_JS
    assert "Math.abs(n)" in COMMON_JS
    assert "Credit carried forward" in COMMON_JS
    assert "Do not pay - credit applied" in COMMON_JS


def test_dashboard_outstanding_kpi_uses_balance_state_copy():
    assert 'id="kpiOutstandingLabel"' in DASHBOARD_TEMPLATE
    assert 'id="kpiOutstandingSub"' in DASHBOARD_TEMPLATE
    assert "LCCA.balanceDisplay(stats.total_outstanding_balance)" in DASHBOARD_TEMPLATE
    assert "Net Credit Balance" in DASHBOARD_TEMPLATE
    assert "Payment required across learners" in DASHBOARD_TEMPLATE


def test_learner_balance_pill_uses_balance_state_helper():
    assert "function balancePill(balance)" in LEARNERS_TEMPLATE
    assert "LCCA.balanceDisplay(balance)" in LEARNERS_TEMPLATE
    assert "display.action" in LEARNERS_TEMPLATE
