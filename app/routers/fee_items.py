from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request

router = APIRouter(prefix="/api/fee-items", tags=["Fee Items"],
                   dependencies=[Depends(auth.require_admin)])


@router.get("", response_model=list[schemas.FeeItemOut])
def list_fee_items(
    active_only: bool = Query(True),
    grade: Optional[str] = Query(None, description="Filter by applicable_grades"),
    db: Session = Depends(get_db),
):
    q = db.query(models.FeeItem)
    if active_only:
        q = q.filter(models.FeeItem.is_active == True)
    if grade:
        q = q.filter(models.FeeItem.applicable_grades.contains(grade))
    return q.order_by(models.FeeItem.sort_order, models.FeeItem.name).all()


@router.post("", response_model=schemas.FeeItemOut, status_code=201)
def create_fee_item(payload: schemas.FeeItemCreate, request: Request, db: Session = Depends(get_db)):
    if payload.amount <= Decimal("0.00"):
        raise HTTPException(400, "Fee item amount must be greater than zero")
    fi = models.FeeItem(**payload.model_dump())
    db.add(fi)
    db.flush()
    audit_from_request(request, db, "CREATE", "FeeItem", fi.id, f"Created: {fi.name}")
    db.commit()
    db.refresh(fi)
    return fi


@router.get("/{fee_item_id}", response_model=schemas.FeeItemOut)
def get_fee_item(fee_item_id: int, db: Session = Depends(get_db)):
    fi = db.query(models.FeeItem).filter(models.FeeItem.id == fee_item_id).first()
    if not fi:
        raise HTTPException(404, "Fee item not found")
    return fi


@router.put("/{fee_item_id}", response_model=schemas.FeeItemOut)
def update_fee_item(fee_item_id: int, payload: schemas.FeeItemUpdate,
                    request: Request, db: Session = Depends(get_db)):
    if payload.amount <= Decimal("0.00"):
        raise HTTPException(400, "Fee item amount must be greater than zero")
    fi = db.query(models.FeeItem).filter(models.FeeItem.id == fee_item_id).first()
    if not fi:
        raise HTTPException(404, "Fee item not found")
    for field, value in payload.model_dump().items():
        if hasattr(fi, field):
            setattr(fi, field, value)
    audit_from_request(request, db, "UPDATE", "FeeItem", fee_item_id, f"Updated: {fi.name}")
    db.commit()
    db.refresh(fi)
    return fi


@router.delete("/{fee_item_id}", status_code=204)
def delete_fee_item(fee_item_id: int, request: Request, db: Session = Depends(get_db)):
    fi = db.query(models.FeeItem).filter(models.FeeItem.id == fee_item_id).first()
    if not fi:
        raise HTTPException(404, "Fee item not found")
    fi.is_active = False
    audit_from_request(request, db, "DELETE", "FeeItem", fee_item_id, f"Deactivated: {fi.name}")
    db.commit()
    return None


@router.get("/learner/{learner_id}/assignments", response_model=list[schemas.LearnerFeeItemOut])
def get_learner_assignments(learner_id: int, db: Session = Depends(get_db)):
    assignments = db.query(models.LearnerFeeItem).join(models.FeeItem).filter(
        models.LearnerFeeItem.learner_id == learner_id,
        models.LearnerFeeItem.is_active == True,
        models.FeeItem.is_active == True,
    ).all()
    result = []
    for a in assignments:
        eff = a.custom_amount if a.custom_amount is not None else a.fee_item.amount
        result.append(schemas.LearnerFeeItemOut(
            id=a.id, fee_item_id=a.fee_item_id,
            fee_item_name=a.fee_item.name,
            custom_amount=a.custom_amount,
            effective_amount=Decimal(str(eff)),
            frequency=a.fee_item.frequency,
            is_active=a.is_active,
        ))
    return result


@router.post("/learner/{learner_id}/assign", status_code=201)
def assign_fee_to_learner(learner_id: int, payload: schemas.AssignFeeItemRequest,
                          request: Request, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    fi = db.query(models.FeeItem).filter(models.FeeItem.id == payload.fee_item_id).first()
    if not fi:
        raise HTTPException(404, "Fee item not found")
    existing = db.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.learner_id == learner_id,
        models.LearnerFeeItem.fee_item_id == payload.fee_item_id,
    ).first()
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.custom_amount = payload.custom_amount
            db.commit()
            return {"message": "Fee item re-activated", "id": existing.id}
        return {"message": "Already assigned", "id": existing.id}
    a = models.LearnerFeeItem(
        learner_id=learner_id, fee_item_id=payload.fee_item_id,
        custom_amount=payload.custom_amount, is_active=True,
        assigned_by=request.session.get("username", "admin"),
    )
    db.add(a)
    audit_from_request(request, db, "CREATE", "LearnerFeeItem", None,
                       f"Assigned {fi.name} to {learner.full_name}")
    db.commit()
    db.refresh(a)
    return {"message": "Fee item assigned", "id": a.id}


@router.delete("/learner/{learner_id}/assignments/{assignment_id}", status_code=204)
def remove_assignment(learner_id: int, assignment_id: int,
                      request: Request, db: Session = Depends(get_db)):
    a = db.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.id == assignment_id,
        models.LearnerFeeItem.learner_id == learner_id,
    ).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    a.is_active = False
    audit_from_request(request, db, "DELETE", "LearnerFeeItem", assignment_id, "Removed fee assignment")
    db.commit()
    return None
