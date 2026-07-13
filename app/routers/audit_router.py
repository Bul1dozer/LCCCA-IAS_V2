from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from .. import models, auth
from ..database import get_db

router = APIRouter(prefix="/api/audit", tags=["Audit"], dependencies=[Depends(auth.require_admin)])

@router.get("")
def list_audit_logs(
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    action: Optional[str] = None,
    username: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(models.AuditLog)
    if resource_type:
        q = q.filter(models.AuditLog.resource_type == resource_type)
    if resource_id:
        q = q.filter(models.AuditLog.resource_id == resource_id)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if username:
        q = q.filter(models.AuditLog.username.ilike(f"%{username}%"))
    total = q.count()
    logs = q.order_by(models.AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "logs": [
            {
                "id": l.id, "username": l.username, "action": l.action,
                "resource_type": l.resource_type, "resource_id": l.resource_id,
                "detail": l.detail, "ip_address": l.ip_address,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }
