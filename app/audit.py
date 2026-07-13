"""
Immutable audit logging — every state-changing action is recorded here.
Call audit() inside any router that modifies data.
"""

from datetime import datetime
from fastapi import Request
from sqlalchemy.orm import Session
from . import models


def audit(
    db: Session,
    action: str,
    resource_type: str = None,
    resource_id=None,
    detail: str = None,
    request: Request = None,
    user_id: int = None,
    username: str = None,
):
    """Write one immutable audit log entry."""
    ip = None
    ua = None
    if request:
        # X-Forwarded-For for reverse-proxy deployments
        xff = request.headers.get("x-forwarded-for")
        ip = xff.split(",")[0].strip() if xff else getattr(request.client, "host", None)
        ua = request.headers.get("user-agent", "")[:255]

    entry = models.AuditLog(
        user_id=user_id,
        username=username or "system",
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        detail=detail,
        ip_address=ip,
        user_agent=ua,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    # Deliberately NOT committing here — caller's transaction includes this entry.
    # This means audits are atomic with the change they describe.


def audit_from_request(request: Request, db: Session, action: str, resource_type: str = None,
                        resource_id=None, detail: str = None):
    """Convenience wrapper that pulls user info from the session."""
    uid = request.session.get("user_id")
    uname = request.session.get("username", "unknown")
    audit(db, action=action, resource_type=resource_type, resource_id=resource_id,
          detail=detail, request=request, user_id=uid, username=uname)
