"""
Learners router — V2 features:
  - 10-digit learner code: YYYYMMDDNN (date of admission + daily sequence)
  - Physical address stored and returned
  - Grade grouping endpoint: GET /api/learners/by-grade
  - Grade 10 in VALID_GRADES
"""
from datetime import datetime, date
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

router = APIRouter(prefix="/api/learners", tags=["Learners"],
                   dependencies=[Depends(auth.require_admin)])

VALID_GRADES = models.VALID_GRADES
GRADE_ORDER = {g: i for i, g in enumerate(VALID_GRADES)}


def _generate_learner_code(db: Session, admission_date) -> str:
    """
    10-digit learner ID: YYYYMMDD (8) + 2-digit daily sequence = 10 digits.
    E.g. 2026071401 = admitted 2026-07-14, 1st learner on that day.
    If more than 99 admissions on one day, uses 3 digits (11 total) — still unique.
    """
    if isinstance(admission_date, str):
        try:
            admission_date = date.fromisoformat(admission_date)
        except Exception:
            admission_date = date.today()
    date_prefix = admission_date.strftime("%Y%m%d")
    existing_count = db.query(models.Learner).filter(
        models.Learner.learner_code.like(f"{date_prefix}%")
    ).count()
    seq = existing_count + 1
    return f"{date_prefix}{seq:02d}"


def _to_out(l: models.Learner) -> schemas.LearnerOut:
    return schemas.LearnerOut.model_validate(l)


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


@router.get("/grades")
def get_grades():
    """Canonical grade list including Grade 10."""
    return {"grades": VALID_GRADES}


@router.get("/by-grade")
def learners_by_grade(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    """
    All learners grouped by grade in canonical order.
    Each group lists learners sorted by class_name then full_name.
    Used by fee catalogue grade-allocation views.
    """
    q = db.query(models.Learner)
    if not include_inactive:
        q = q.filter(models.Learner.is_active == True)
    all_learners = q.all()

    groups = {g: [] for g in VALID_GRADES}
    other = []
    for l in all_learners:
        if l.grade in groups:
            groups[l.grade].append(l)
        else:
            other.append(l)

    result = []
    for grade in VALID_GRADES:
        learners_in_grade = sorted(groups[grade], key=lambda x: (x.class_name, x.full_name))
        result.append({
            "grade": grade,
            "learner_count": len(learners_in_grade),
            "learners": [schemas.LearnerOut.model_validate(l).model_dump() for l in learners_in_grade],
        })
    if other:
        result.append({
            "grade": "Other",
            "learner_count": len(other),
            "learners": [schemas.LearnerOut.model_validate(l).model_dump()
                         for l in sorted(other, key=lambda x: x.full_name)],
        })
    return result


@router.get("", response_model=list[schemas.LearnerOut])
def list_learners(
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    grade: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
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
    if grade:
        q = q.filter(models.Learner.grade == grade)
    return q.order_by(models.Learner.full_name).all()


@router.post("", response_model=schemas.LearnerOut, status_code=201)
def create_learner(payload: schemas.LearnerCreate, request: Request, db: Session = Depends(get_db)):
    learner = models.Learner(
        learner_code=_generate_learner_code(db, payload.date_of_admission),
        full_name=payload.full_name, grade=payload.grade,
        class_name=payload.class_name, date_of_admission=payload.date_of_admission,
        status=payload.status, balance=Decimal("0.00"),
        physical_address=payload.physical_address,
        id_number=payload.id_number,
        date_of_birth=payload.date_of_birth,
    )
    db.add(learner)
    db.flush()
    auto_assign_mandatory_fees(db, learner, assigned_by=request.session.get("username", "admin"))
    audit_from_request(request, db, "CREATE", "Learner", learner.id,
                       f"Created: {learner.full_name} ({learner.learner_code})")
    db.commit()
    db.refresh(learner)
    return learner


@router.get("/{learner_id}", response_model=schemas.LearnerDetail)
def get_learner(learner_id: int, db: Session = Depends(get_db)):
    l = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not l:
        raise HTTPException(404, "Learner not found")
    return _to_detail(l)


@router.put("/{learner_id}", response_model=schemas.LearnerOut)
def update_learner(learner_id: int, payload: schemas.LearnerUpdate,
                   request: Request, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    old_grade = learner.grade
    for field, value in payload.model_dump(exclude_unset=False).items():
        if hasattr(learner, field):
            setattr(learner, field, value)
    if learner.grade != old_grade:
        auto_assign_mandatory_fees(db, learner, assigned_by=request.session.get("username", "admin"))
    audit_from_request(request, db, "UPDATE", "Learner", learner_id,
                       f"Updated: {learner.full_name}")
    db.commit()
    db.refresh(learner)
    return learner


@router.delete("/{learner_id}", status_code=204)
def delete_learner(learner_id: int, request: Request, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(
        models.Learner.id == learner_id, models.Learner.is_active == True
    ).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    balance = compute_balance(learner_id, db)
    if balance != 0:
        raise HTTPException(400,
            f"Cannot deactivate learner with outstanding balance N$ {balance:,.2f}. "
            "Settle or write off the balance first.")
    username = request.session.get("username", "system")
    learner.is_active = False
    learner.status = "Inactive"
    learner.deleted_at = datetime.utcnow()
    learner.deleted_by = username
    audit_from_request(request, db, "DELETE", "Learner", learner_id,
                       f"Soft-deleted: {learner.full_name}")
    db.commit()
    return None
