"""LCCA-IAS v4 — FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .database import engine, Base, get_session
from . import models  # noqa: ensure models registered
from . import auth
from .routers import (
    auth as auth_router,
    audit_router,
    email_logs,
    fees,
    imports,
    learners,
    invoices,
    month_end,
    parents,
    payments,
    fee_items,
    dashboard,
    recon,
    reports,
    settings,
)
from .seed_data import seed


Base.metadata.create_all(bind=engine)


def _cors_origins():
    raw = auth.os.environ.get("LCCA_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if origins:
        return origins
    if auth.IS_PRODUCTION:
        raise RuntimeError("LCCA_CORS_ORIGINS must be set when LCCA_ENV=production.")
    return ["http://localhost:8000", "http://127.0.0.1:8000"]


app = FastAPI(
    title="LCCA-IAS",
    description="Life Changing Christian Church Academy — Invoice Automation System",
    version="4.0.0",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SECRET_KEY,
    session_cookie="lcca_session",
    max_age=auth.env_int("LCCA_SESSION_MAX_AGE", 8 * 60 * 60),
    same_site=auth.os.environ.get("LCCA_SESSION_SAMESITE", "lax"),
    https_only=auth.env_bool("LCCA_SESSION_COOKIE_SECURE", auth.IS_PRODUCTION),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

templates = Jinja2Templates(directory="templates")


CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/login/2fa",
    "/api/auth/forgot-password",
}


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    safe_method = request.method in {"GET", "HEAD", "OPTIONS"}
    api_mutation = request.url.path.startswith("/api/") and not safe_method
    csrf_required = (
        api_mutation
        and request.url.path not in CSRF_EXEMPT_PATHS
        and auth.CSRF_COOKIE_NAME in request.cookies
    )

    if csrf_required:
        try:
            auth.validate_csrf_request(request)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 403)
            detail = getattr(exc, "detail", "Invalid CSRF token.")
            return JSONResponse({"detail": detail}, status_code=status_code)

    auth.ensure_csrf_token(request)
    response = await call_next(request)
    response.set_cookie(
        auth.CSRF_COOKIE_NAME,
        request.state.csrf_token,
        httponly=False,
        secure=auth.env_bool("LCCA_SESSION_COOKIE_SECURE", auth.IS_PRODUCTION),
        samesite=auth.os.environ.get("LCCA_SESSION_SAMESITE", "lax"),
        max_age=auth.env_int("LCCA_SESSION_MAX_AGE", 8 * 60 * 60),
    )
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")


# API routers
app.include_router(auth_router.router)
app.include_router(audit_router.router)
app.include_router(email_logs.router)
app.include_router(fees.router)
app.include_router(imports.router)
app.include_router(learners.router)
app.include_router(invoices.router)
app.include_router(month_end.router)
app.include_router(parents.router)
app.include_router(payments.router)
app.include_router(fee_items.router)
app.include_router(dashboard.router)
app.include_router(recon.router)
app.include_router(reports.router)
app.include_router(settings.router)


PAGE_ROUTES = {
    "/dashboard": ("dashboard.html", "dashboard"),
    "/learners": ("learners.html", "learners"),
    "/parents": ("parents.html", "parents"),
    "/fee-items": ("fee_items.html", "fee-items"),
    "/fees": ("fees.html", "fees"),
    "/payments": ("payments.html", "payments"),
    "/invoices": ("invoices.html", "invoices"),
    "/email-logs": ("email_logs.html", "email-logs"),
    "/recon": ("recon.html", "recon"),
    "/reports": ("reports.html", "reports"),
    "/audit": ("audit.html", "audit"),
    "/month-end": ("month_end.html", "month-end"),
    "/imports": ("imports.html", "imports"),
    "/settings": ("settings.html", "settings"),
}


@app.on_event("startup")
def startup():
    with get_session() as db:
        seed(db)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/login")


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={},
    )


for path, (template_name, active_page) in PAGE_ROUTES.items():
    async def page(request: Request, template_name=template_name, active_page=active_page):
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context={
                "active_page": active_page,
                "username": request.session.get("username"),
            },
        )

    app.add_api_route(path, page, methods=["GET"], include_in_schema=False)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "system": "LCCA-IAS",
        "version": "4.0.0",
    }
