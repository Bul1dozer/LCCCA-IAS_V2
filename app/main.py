"""LCCA-IAS v4 — FastAPI application entry point."""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from .database import engine, Base
from . import models  # noqa: ensure models registered
from . import auth
from .routers import auth as auth_router, learners, parents, invoices, payments, fee_items, dashboard
from .seed_data import seed
from .database import get_session

Base.metadata.create_all(bind=engine)

app = FastAPI(title="LCCA-IAS", description="Life Changing Christian Church Academy — Invoice Automation System", version="4.0.0")

app.add_middleware(SessionMiddleware, secret_key=auth.SECRET_KEY, session_cookie="lcca_session")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# Routers
app.include_router(auth_router.router)
app.include_router(learners.router)
app.include_router(parents.router)
app.include_router(invoices.router)
app.include_router(payments.router)
app.include_router(fee_items.router)
app.include_router(dashboard.router)


@app.on_event("startup")
def startup():
    with get_session() as db:
        seed(db)


@app.get("/health")
def health():
    return {"status": "ok", "system": "LCCA-IAS", "version": "4.0.0"}
