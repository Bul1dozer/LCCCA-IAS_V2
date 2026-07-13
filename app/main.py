"""
LCCA-IAS v2 — Life Changing Christian Church Academy
School Fees Management & Invoice Automation System

Run:  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
Login: admin / admin123
"""

import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import models, auth
from .database import engine, SessionLocal, ensure_schema
from .routers import (
    auth as auth_router, dashboard, learners, parents, fees,
    payments, invoices, reports, email_logs,
)
from .routers import fee_items, audit_router, month_end, imports, recon, settings
from .seed_data import seed_if_empty

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="LCCA Invoice Automation System",
    description="Life Changing Christian Church Academy — School Fees Management v2",
    version="2.0.0",
)

# Session middleware
app.add_middleware(SessionMiddleware, secret_key=auth.SECRET_KEY, session_cookie="lcca_session")

# Static files & templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# DB init + seed
models.Base.metadata.create_all(bind=engine)
ensure_schema()
with SessionLocal() as db:
    seed_if_empty(db)

# Scheduler startup/shutdown
@app.on_event("startup")
async def on_startup():
    from .scheduler import start_scheduler
    start_scheduler()

@app.on_event("shutdown")
async def on_shutdown():
    from .scheduler import stop_scheduler
    stop_scheduler()

# Register all routers
for r in [
    auth_router.router, dashboard.router, learners.router, parents.router,
    fees.router, payments.router, invoices.router, reports.router, email_logs.router,
    fee_items.router, audit_router.router, month_end.router, imports.router,
    recon.router, settings.router,
]:
    app.include_router(r)


# ---- Page routes ----

def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get("user_id"))


@app.get("/")
def root(request: Request):
    return RedirectResponse("/dashboard" if _is_authenticated(request) else "/login")


@app.get("/login")
def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(request, "login.html", {})


PROTECTED_PAGES = {
    "dashboard":    "dashboard.html",
    "learners":     "learners.html",
    "parents":      "parents.html",
    "fees":         "fees.html",
    "fee-items":    "fee_items.html",
    "payments":     "payments.html",
    "invoices":     "invoices.html",
    "email-logs":   "email_logs.html",
    "reports":      "reports.html",
    "month-end":    "month_end.html",
    "imports":      "imports.html",
    "recon":        "recon.html",
    "audit":        "audit.html",
    "settings":     "settings.html",
}


def _protected(page_key: str, request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request, PROTECTED_PAGES[page_key],
        {"active_page": page_key, "username": request.session.get("username", "Administrator")},
    )


@app.get("/dashboard")
def dashboard_page(request: Request): return _protected("dashboard", request)

@app.get("/learners")
def learners_page(request: Request): return _protected("learners", request)

@app.get("/parents")
def parents_page(request: Request): return _protected("parents", request)

@app.get("/fees")
def fees_page(request: Request): return _protected("fees", request)

@app.get("/fee-items")
def fee_items_page(request: Request): return _protected("fee-items", request)

@app.get("/payments")
def payments_page(request: Request): return _protected("payments", request)

@app.get("/invoices")
def invoices_page(request: Request): return _protected("invoices", request)

@app.get("/email-logs")
def email_logs_page(request: Request): return _protected("email-logs", request)

@app.get("/reports")
def reports_page(request: Request): return _protected("reports", request)

@app.get("/month-end")
def month_end_page(request: Request): return _protected("month-end", request)

@app.get("/imports")
def imports_page(request: Request): return _protected("imports", request)

@app.get("/recon")
def recon_page(request: Request): return _protected("recon", request)

@app.get("/audit")
def audit_page(request: Request): return _protected("audit", request)

@app.get("/settings")
def settings_page(request: Request): return _protected("settings", request)

@app.get("/health")
def health_check():
    return {"status": "ok", "system": "LCCA-IAS", "version": "2.0.0"}
