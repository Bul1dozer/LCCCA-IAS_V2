from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/api/email-logs", tags=["Email Logs"], dependencies=[Depends(auth.require_admin)])


@router.get("", response_model=list[schemas.EmailLogOut])
def list_email_logs(db: Session = Depends(get_db)):
    logs = db.query(models.EmailLog).options(
        joinedload(models.EmailLog.invoice).joinedload(models.Invoice.learner)
    ).order_by(models.EmailLog.sent_at.desc()).all()

    results = []
    for log in logs:
        data = schemas.EmailLogOut.model_validate(log).model_dump()
        if log.invoice:
            data["invoice_number"] = log.invoice.invoice_number
            data["learner_name"] = log.invoice.learner.full_name if log.invoice.learner else None
        results.append(schemas.EmailLogOut(**data))
    return results
