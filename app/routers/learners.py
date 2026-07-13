from datetime import datetime
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request
from ..fee_engine import auto_assign_mandatory_fees
from ..ledger import compute_balance

router = APIRouter(prefix="/api/learners", tags=["Learners"], dependencies=[Depends(auth.require_admin)])

VALID_GRADES = [
    "Baby Class", "Toddler Class", "Grade 0", "Grade 1", "Grade 2",
    "Grade 3", "Grade 4", "Grade 5", "Grade 6", "Grade 7", "Grade 8", "Grade 9",
]


def _generate_learner_code(db: Session) -> str:
    from datetime import datetime
    year = datetime.now().year
    count = db.query(models.Learner).count() + 1
    return f"LCCA-{year}-{count:04d}"


def _set_learner_id_if_missing(learner: models.Learner, db: Session) -> None:
    if learner.learner_id:
        return
    if not learner.date_of_admission:
        return
    prefix = learner.date_of_admission.strftime("%y%m%d")
    count = db.query(models.Learner).filter(
        models.Learner.date_of_admission == learner.date_of_admission,
        models.Learner.learner_id.isnot(None),
    ).count() + 1
    learner.learner_id = f"{prefix}{count:04d}"


def _to_detail(learner: models.Learner) -> schemas.LearnerDetail:
    parents = []
    for rel in learner.relationships_:
        parents.append(schemas.ParentMini(
            id=rel.parent.id, full_name=rel.parent.full_name,
            email=rel.parent.email, phone=rel.parent.phone,
            relationship_type=rel.relationship_type, relationship_id=rel.id,
        ))
    data = schemas.LearnerOut.model_validate(learner).model_dump()
    data["parents"] = parents
    return schemas.LearnerDetail(**data)


@router.get("", response_model=list[schemas.LearnerOut])
def list_learners(
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    include_inactive: bool = Query(False, description="Include soft-deleted learners"),
    db: Session = Depends(get_db),
):
    q = db.query(models.Learner)
    if not include_inactive:
        q = q.filter(models.Learner.is_active == True)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            models.Learner.full_name.ilike(like),
            models.Learner.learner_code.ilike(like),
            models.Learner.grade.ilike(like),
            models.Learner.class_name.ilike(like),
        ))
    if status_filter:
        q = q.filter(models.Learner.status == status_filter)
    return q.order_by(models.Learner.full_name).all()


@router.get("/grades")
def get_grades():
    """Return the canonical grade list for dropdowns."""
    return {"grades": VALID_GRADES}


@router.post("", response_model=schemas.LearnerOut, status_code=201)
def create_learner(payload: schemas.LearnerCreate, request: Request, db: Session = Depends(get_db)):
    learner = models.Learner(
        learner_code=_generate_learner_code(db),
        full_name=payload.full_name, grade=payload.grade,
        class_name=payload.class_name, date_of_admission=payload.date_of_admission,
        status=payload.status, balance=Decimal("0.00"),
        physical_address=payload.physical_address,
    )
    db.add(learner)
    db.flush()
    _set_learner_id_if_missing(learner, db)
    auto_assign_mandatory_fees(db, learner, assigned_by=request.session.get("username", "admin"))
    audit_from_request(request, db, "CREATE", "Learner", learner.id,
                       f"Created learner: {learner.full_name} ({learner.learner_code})")
    db.commit()
    db.refresh(learner)
    return learner


@router.get("/{learner_id}", response_model=schemas.LearnerDetail)
def get_learner(learner_id: int, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    return _to_detail(learner)


@router.put("/{learner_id}", response_model=schemas.LearnerOut)
def update_learner(learner_id: int, payload: schemas.LearnerUpdate, request: Request, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    old_grade = learner.grade
    for field, value in payload.model_dump().items():
        setattr(learner, field, value)
    _set_learner_id_if_missing(learner, db)
    # If grade changed, auto-assign new mandatory fees
    if learner.grade != old_grade:
        auto_assign_mandatory_fees(db, learner, assigned_by=request.session.get("username", "admin"))
    audit_from_request(request, db, "UPDATE", "Learner", learner_id,
                       f"Updated learner: {learner.full_name}")
    db.commit()
    db.refresh(learner)
    return learner


@router.delete("/{learner_id}", status_code=204)
def delete_learner(learner_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Soft-deletes a learner. Hard delete is not permitted for financial
    integrity — historical invoices/payments/ledger entries must survive.
    A learner with an outstanding non-zero balance cannot be deactivated
    without an explicit override, to avoid silently losing visibility
    of money owed.
    """
    learner = db.query(models.Learner).filter(
        models.Learner.id == learner_id,
        models.Learner.is_active == True,
    ).first()
    if not learner:
        raise HTTPException(404, "Learner not found")

    balance = compute_balance(learner_id, db)
    if balance != 0:
        raise HTTPException(
            400,
            f"Cannot deactivate learner with outstanding balance of N$ {balance:,.2f}. "
            "Settle the account or write off the balance first."
        )

    username = request.session.get("username", "system")
    learner.is_active = False
    learner.status = "Inactive"
    learner.deleted_at = datetime.utcnow()
    learner.deleted_by = username

    audit_from_request(request, db, "DELETE", "Learner", learner_id,
                       f"Soft-deleted learner: {learner.full_name} ({learner.learner_code})")
    db.commit()
    return None
