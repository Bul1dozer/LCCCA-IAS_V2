"""
PDF generation for LCCA-IAS using ReportLab.

Produces:
    - build_invoice_pdf()           -> branded learner invoice
    - build_receipt_pdf()           -> payment receipt
    - build_outstanding_report_pdf()-> outstanding balances report
    - build_collections_report_pdf()-> monthly collections report
    - build_statement_pdf()         -> learner account statement
    - build_payment_history_pdf()   -> full payment history report

All functions return raw PDF bytes (BytesIO buffer contents) so they can
be streamed directly as a FastAPI Response / StreamingResponse.
"""

import os
from io import BytesIO
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

NAVY = colors.HexColor("#102A59")
GOLD = colors.HexColor("#D4AF37")
LIGHT_GREY = colors.HexColor("#F4F6FA")
DARK_GREY = colors.HexColor("#444444")

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "img", "logo.png")

SCHOOL_NAME = "Life Changing Christian Church Academy"
SCHOOL_TAGLINE = "Raising godly Champions"
SCHOOL_ADDRESS = "Invokavit Str, Gemeente 1, Katutura, Windhoek, Namibia | Tel: 081 749 6077 / 085 551 5495"
SCHOOL_EMAIL = "info@lcccacademy.com"

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    "TitleStyle", parent=styles["Heading1"], fontSize=18, textColor=NAVY, spaceAfter=2,
)
sub_style = ParagraphStyle(
    "SubStyle", parent=styles["Normal"], fontSize=9, textColor=DARK_GREY,
)
section_style = ParagraphStyle(
    "SectionStyle", parent=styles["Heading3"], fontSize=11, textColor=NAVY, spaceBefore=10, spaceAfter=4,
)
normal_style = ParagraphStyle(
    "NormalStyle", parent=styles["Normal"], fontSize=9.5, leading=14,
)
right_style = ParagraphStyle(
    "RightStyle", parent=styles["Normal"], fontSize=9.5, alignment=TA_RIGHT,
)
center_style = ParagraphStyle(
    "CenterStyle", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER, textColor=DARK_GREY,
)


def _currency(value) -> str:
    try:
        return f"N$ {float(value):,.2f}"
    except (TypeError, ValueError):
        return "N$ 0.00"


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _currency_abs(value) -> str:
    return _currency(abs(_money(value)))


def _fmt_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%d %b %Y")
    return str(d)


def _fmt_due_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%d/%m/%Y")
    return str(d)


def _invoice_balance_presentation(invoice) -> dict:
    balance = _money(getattr(invoice, "outstanding_balance", 0))
    if balance > 0:
        return {
            "summary_label": "Outstanding Balance Due",
            "summary_amount": _currency(balance),
            "notice": f"PAYMENT DUE BY: {_fmt_due_date(invoice.due_date)}",
            "message": (
                "Kindly settle the outstanding balance on or before the due date. "
                "Please use <b>{reference}</b> as your payment reference."
            ),
            "background": GOLD,
        }
    if balance < 0:
        return {
            "summary_label": "Credit Balance Applied",
            "summary_amount": _currency_abs(balance),
            "notice": "DO NOT PAY - CREDIT APPLIED",
            "message": (
                "Your account is in credit. This amount will be carried forward "
                "and applied to the next invoice."
            ),
            "background": colors.HexColor("#EAF2FB"),
        }
    return {
        "summary_label": "Account Settled",
        "summary_amount": _currency(0),
        "notice": "NO PAYMENT REQUIRED",
        "message": "This account is settled for the current invoice.",
        "background": colors.HexColor("#E7F6EE"),
    }


def _header_block(report_title: str):
    """Returns a list of flowables forming the branded header."""
    elements = []

    logo_cell = ""
    if os.path.exists(LOGO_PATH):
        logo_cell = Image(LOGO_PATH, width=22 * mm, height=22 * mm)

    text_cell = [
        Paragraph(SCHOOL_NAME, title_style),
        Paragraph(SCHOOL_TAGLINE, sub_style),
        Paragraph(SCHOOL_ADDRESS, sub_style),
        Paragraph(SCHOOL_EMAIL, sub_style),
    ]

    header_table = Table(
        [[logo_cell, text_cell]],
        colWidths=[28 * mm, 150 * mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6))
    elements.append(HRFlowable(width="100%", thickness=2, color=GOLD))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph(report_title, ParagraphStyle(
        "ReportTitle", parent=styles["Heading2"], fontSize=14, textColor=NAVY, spaceAfter=8,
    )))
    return elements


def _footer_note(text="Thank you for your continued partnership in your child's Christian education."):
    return [
        Spacer(1, 14),
        HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#CCCCCC")),
        Spacer(1, 4),
        Paragraph(text, center_style),
        Paragraph(
            f"Generated on {datetime.now().strftime('%d %b %Y %H:%M')} "
            f"by LCCA Invoice Automation System (LCCA-IAS)",
            center_style,
        ),
    ]


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
def build_invoice_pdf(invoice, learner, parents, items) -> bytes:
    """
    invoice : models.Invoice
    learner : models.Learner
    parents : list[models.Parent]
    items   : list[models.InvoiceItem]
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )

    elements = _header_block(f"INVOICE #{invoice.invoice_number}")

    # Invoice meta + learner / parent info side by side
    if learner:
        learner_info = [
            Paragraph("<b>Learner Information</b>", normal_style),
            Paragraph(f"Name: {learner.full_name}", normal_style),
            Paragraph(f"Learner ID: {learner.learner_code}", normal_style),
            Paragraph(f"Grade / Class: {learner.grade} - {learner.class_name}", normal_style),
            Paragraph(f"Status: {learner.status}", normal_style),
        ]
    else:
        linked_names = []
        if getattr(invoice, "parent", None):
            item_learner_ids = {item.learner_id for item in items if item.learner_id}
            for rel in invoice.parent.relationships_:
                if rel.learner and (not item_learner_ids or rel.learner.id in item_learner_ids):
                    linked_names.append(rel.learner.full_name)
        learner_info = [
            Paragraph("<b>Billing Scope</b>", normal_style),
            Paragraph("Parent aggregated invoice", normal_style),
            Paragraph(f"Linked Learners: {', '.join(linked_names) if linked_names else 'See line items'}", normal_style),
            Paragraph(f"Billing Period: {invoice.billing_period or 'N/A'}", normal_style),
        ]

    if parents:
        parent_lines = ["<b>Parent / Guardian Information</b>"]
        for p in parents:
            parent_lines.append(f"{p.full_name} ({p.email or 'no email'} / {p.phone or 'no phone'})")
        parent_info = [Paragraph(line, normal_style) for line in parent_lines]
    else:
        parent_info = [Paragraph("<b>Parent / Guardian Information</b>", normal_style),
                        Paragraph("No parent/guardian linked.", normal_style)]

    invoice_meta = [
        Paragraph("<b>Invoice Details</b>", normal_style),
        Paragraph(f"Invoice Date: {_fmt_date(invoice.issue_date)}", normal_style),
        Paragraph(f"Due Date: {_fmt_date(invoice.due_date)}", normal_style),
        Paragraph(f"Status: {invoice.status}", normal_style),
    ]

    info_table = Table(
        [[learner_info, parent_info], [invoice_meta, ""]],
        colWidths=[90 * mm, 88 * mm],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("SPAN", (0, 1), (1, 1)),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 14))

    # Charges table
    elements.append(Paragraph("Current Charges", section_style))
    charge_rows = [["Description", "Amount"]]
    for item in items:
        charge_rows.append([item.description, _currency(item.amount)])
    if not items:
        charge_rows.append(["No new charges on this invoice", _currency(0)])
    charge_rows.append(["Total Current Charges", _currency(invoice.current_charges)])

    charges_table = Table(charge_rows, colWidths=[130 * mm, 48 * mm])
    charges_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(charges_table)
    elements.append(Spacer(1, 14))

    # Summary table: previous balance, current charges, payments, outstanding
    elements.append(Paragraph("Account Summary", section_style))
    balance_state = _invoice_balance_presentation(invoice)
    summary_rows = [
        ["Previous Balance Brought Forward", _currency(invoice.previous_balance)],
        ["Current Charges (this invoice)", _currency(invoice.current_charges)],
        ["Payments Made (since last invoice)", f"({_currency(invoice.payments_made)})"],
        [balance_state["summary_label"], balance_state["summary_amount"]],
    ]
    summary_table = Table(summary_rows, colWidths=[130 * mm, 48 * mm])
    summary_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), balance_state["background"]),
        ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 10))

    reference = learner.learner_code if learner else invoice.invoice_number
    elements.append(Paragraph(
        f"<b>{balance_state['notice']}</b> &mdash; "
        f"{balance_state['message'].format(reference=reference)}",
        normal_style,
    ))

    elements.extend(_footer_note())
    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Receipt
# ---------------------------------------------------------------------------
def build_receipt_pdf(payment, learner) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    elements = _header_block(f"PAYMENT RECEIPT #{payment.id:06d}")

    rows = [
        ["Learner Name", learner.full_name],
        ["Learner ID", learner.learner_code],
        ["Grade / Class", f"{learner.grade} - {learner.class_name}"],
        ["Date Paid", _fmt_date(payment.date_paid)],
        ["Payment Method", payment.payment_method],
        ["Reference Number", payment.reference_number or "N/A"],
        ["Amount Paid", _currency(payment.amount_paid)],
        ["New Outstanding Balance", _currency(learner.balance)],
    ]
    table = Table(rows, colWidths=[60 * mm, 118 * mm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREY),
        ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -2), (-1, -1), GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 10))
    if payment.notes:
        elements.append(Paragraph(f"<b>Notes:</b> {payment.notes}", normal_style))

    elements.extend(_footer_note("This receipt confirms payment received. Please retain it for your records."))
    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Outstanding Balances Report
# ---------------------------------------------------------------------------
def build_outstanding_report_pdf(rows, total_outstanding) -> bytes:
    """rows: list of dicts with learner_code, full_name, grade, class_name, balance"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    elements = _header_block("OUTSTANDING BALANCES REPORT")

    table_rows = [["Learner ID", "Full Name", "Grade", "Class", "Outstanding Balance"]]
    for r in rows:
        table_rows.append([r["learner_code"], r["full_name"], r["grade"], r["class_name"], _currency(r["balance"])])
    table_rows.append(["", "", "", "TOTAL", _currency(total_outstanding)])

    table = Table(table_rows, colWidths=[28 * mm, 60 * mm, 28 * mm, 28 * mm, 34 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD),
        ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Total learners with outstanding balances: {len(rows)}", normal_style))

    elements.extend(_footer_note("Internal report for administrative use."))
    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Monthly Collections Report
# ---------------------------------------------------------------------------
def build_collections_report_pdf(rows, total_collected, period_label) -> bytes:
    """rows: list of dicts with date_paid, learner_name, learner_code, payment_method, reference_number, amount_paid"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    elements = _header_block(f"MONTHLY COLLECTIONS REPORT — {period_label}")

    table_rows = [["Date", "Learner", "Learner ID", "Method", "Reference", "Amount"]]
    for r in rows:
        table_rows.append([
            _fmt_date(r["date_paid"]), r["learner_name"], r["learner_code"],
            r["payment_method"], r["reference_number"] or "-", _currency(r["amount_paid"]),
        ])
    table_rows.append(["", "", "", "", "TOTAL", _currency(total_collected)])

    table = Table(table_rows, colWidths=[22 * mm, 46 * mm, 22 * mm, 28 * mm, 28 * mm, 32 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD),
        ("ALIGN", (5, 0), (5, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Total transactions: {len(rows)}", normal_style))

    elements.extend(_footer_note("Internal report for administrative use."))
    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Learner Account Statement
# ---------------------------------------------------------------------------
def build_statement_pdf(learner, invoices, payments) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    elements = _header_block("LEARNER ACCOUNT STATEMENT")

    info_rows = [
        ["Learner Name", learner.full_name, "Learner ID", learner.learner_code],
        ["Grade / Class", f"{learner.grade} - {learner.class_name}", "Status", learner.status],
        ["Date of Admission", _fmt_date(learner.date_of_admission), "Current Balance", _currency(learner.balance)],
    ]
    info_table = Table(info_rows, colWidths=[35 * mm, 58 * mm, 35 * mm, 58 * mm])
    info_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREY),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 12))

    # Build combined transaction ledger sorted by date
    ledger = []
    for inv in invoices:
        ledger.append({
            "date": inv.issue_date,
            "desc": f"Invoice {inv.invoice_number} - Charges Applied",
            "debit": inv.current_charges,
            "credit": 0,
        })
        if inv.payments_made:
            ledger.append({
                "date": inv.issue_date,
                "desc": f"Payments applied prior to Invoice {inv.invoice_number}",
                "debit": 0,
                "credit": inv.payments_made,
            })
    for p in payments:
        ledger.append({
            "date": p.date_paid,
            "desc": f"Payment Received ({p.payment_method}, Ref: {p.reference_number or 'N/A'})",
            "debit": 0,
            "credit": p.amount_paid,
        })
    
    ledger.sort(key=lambda x: x["date"])

    elements.append(Paragraph("Transaction History", section_style))
    table_rows = [["Date", "Description", "Charges (Debit)", "Payments (Credit)"]]

    running = Decimal("0.00")

    for entry in ledger:
        debit = Decimal(str(entry.get("debit") or "0"))
        credit = Decimal(str(entry.get("credit") or "0"))

        running += debit - credit

        table_rows.append([
            _fmt_date(entry["date"]),
            entry["desc"],
            _currency(debit) if debit else "-",
            _currency(credit) if credit else "-",
        ])

    if not ledger:
        table_rows.append(["-", "No transactions recorded yet.", "-", "-"])

    table_rows.append(["", "Closing Balance", "", _currency(learner.balance)])

    table = Table(table_rows, colWidths=[24 * mm, 86 * mm, 32 * mm, 34 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD),
        ("ALIGN", (2, 0), (3, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements.append(table)

    elements.extend(_footer_note("Please contact the school's accounts office for any account queries."))
    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Payment History Report
# ---------------------------------------------------------------------------
def build_payment_history_pdf(rows, total) -> bytes:
    """rows: list of dicts with date_paid, learner_name, learner_code, payment_method, reference_number, amount_paid"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    elements = _header_block("PAYMENT HISTORY REPORT — ALL RECORDS")

    table_rows = [["Date", "Learner", "Learner ID", "Method", "Reference", "Amount"]]
    for r in rows:
        table_rows.append([
            _fmt_date(r["date_paid"]), r["learner_name"], r["learner_code"],
            r["payment_method"], r["reference_number"] or "-", _currency(r["amount_paid"]),
        ])
    table_rows.append(["", "", "", "", "TOTAL", _currency(total)])

    table = Table(table_rows, colWidths=[22 * mm, 46 * mm, 22 * mm, 28 * mm, 28 * mm, 32 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD),
        ("ALIGN", (5, 0), (5, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Total transactions: {len(rows)}", normal_style))

    elements.extend(_footer_note("Internal report for administrative use."))
    doc.build(elements)
    return buffer.getvalue()
