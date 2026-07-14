from . import models

def audit(db, action, resource_type=None, resource_id=None, detail=None,
          request=None, user_id=None, username="system"):
    ip = None
    ua = None
    if request:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent", "")[:255]
    log = models.AuditLog(
        user_id=user_id, username=username, action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        detail=detail, ip_address=ip, user_agent=ua,
    )
    db.add(log)

def audit_from_request(request, db, action, resource_type=None, resource_id=None, detail=None):
    uid = request.session.get("user_id")
    uname = request.session.get("username", "system")
    audit(db, action, resource_type, resource_id, detail,
          request=request, user_id=uid, username=uname)
