from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request
from ..fee_engine import auto_assign_mandatory_fees, effective_amount

router = APIRouter(prefix="/api/fee-items", tags=["Fee Items"],
                   dependencies=[Depends(auth.require_admin)])


class TransportRoutePayload(BaseModel):
    name: str
    description: Optional[str] = None
    monthly_fee: Decimal = Decimal("0.00")
    is_active: bool = True


def _route_out(route: models.TransportRoute) -> dict:
    return {
        "id": route.id,
        "name": route.name,
        "description": route.description,
        "monthly_fee": route.monthly_fee,
        "is_active": route.is_active,
        "created_at": route.created_at.isoformat() if route.created_at else None,
    }


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


@router.get("/transport/routes")
def list_transport_routes(db: Session = Depends(get_db)):
    routes = db.query(models.TransportRoute).order_by(models.TransportRoute.name).all()
    return [_route_out(route) for route in routes]


@router.post("/transport/routes", status_code=201)
def create_transport_route(
    payload: TransportRoutePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    if payload.monthly_fee < Decimal("0.00"):
        raise HTTPException(400, "Transport route monthly fee cannot be negative")
    route = models.TransportRoute(
        name=payload.name.strip(),
        description=payload.description,
        monthly_fee=payload.monthly_fee,
        is_active=payload.is_active,
    )
    if not route.name:
        raise HTTPException(400, "Transport route name is required")
    db.add(route)
    db.flush()
    audit_from_request(request, db, "CREATE", "TransportRoute", route.id, f"Created: {route.name}")
    db.commit()
    db.refresh(route)
    return _route_out(route)


@router.put("/transport/routes/{route_id}")
def update_transport_route(
    route_id: int,
    payload: TransportRoutePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    if payload.monthly_fee < Decimal("0.00"):
        raise HTTPException(400, "Transport route monthly fee cannot be negative")
    route = db.query(models.TransportRoute).filter(models.TransportRoute.id == route_id).first()
    if not route:
        raise HTTPException(404, "Transport route not found")
    route.name = payload.name.strip()
    route.description = payload.description
    route.monthly_fee = payload.monthly_fee
    route.is_active = payload.is_active
    if not route.name:
        raise HTTPException(400, "Transport route name is required")
    audit_from_request(request, db, "UPDATE", "TransportRoute", route.id, f"Updated: {route.name}")
    db.commit()
    db.refresh(route)
    return _route_out(route)


@router.get("/learner/{learner_id}/profile")
def learner_fee_profile(learner_id: int, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    assignments = (
        db.query(models.LearnerFeeItem)
        .join(models.FeeItem)
        .filter(models.LearnerFeeItem.learner_id == learner_id)
        .order_by(models.FeeItem.sort_order, models.FeeItem.name)
        .all()
    )
    rows = []
    total_monthly = Decimal("0.00")
    for assignment in assignments:
        amount = effective_amount(assignment)
        if assignment.is_active and assignment.fee_item.is_active and assignment.fee_item.frequency == "monthly":
            total_monthly += amount
        rows.append({
            "id": assignment.id,
            "fee_item_id": assignment.fee_item_id,
            "fee_name": assignment.fee_item.name,
            "fee_frequency": assignment.fee_item.frequency,
            "base_amount": assignment.fee_item.amount,
            "effective_amount": amount,
            "custom_amount": assignment.custom_amount,
            "is_mandatory": assignment.fee_item.is_mandatory,
            "is_active": assignment.is_active and assignment.fee_item.is_active,
        })
    return {
        "learner_id": learner.id,
        "learner_code": learner.learner_code,
        "learner_name": learner.full_name,
        "grade": learner.grade,
        "class_name": learner.class_name,
        "total_monthly": total_monthly,
        "assignments": rows,
    }


@router.post("/learner/{learner_id}/auto-assign")
def auto_assign_fees_to_learner(
    learner_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    assigned = auto_assign_mandatory_fees(
        db,
        learner,
        assigned_by=request.session.get("username", "admin"),
    )
    audit_from_request(request, db, "CREATE", "LearnerFeeItem", None,
                       f"Auto-assigned {assigned} mandatory fee(s) to {learner.full_name}")
    db.commit()
    return {"assigned": assigned}


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


@router.put("/{fee_item_id}/toggle", response_model=schemas.FeeItemOut)
def toggle_fee_item(fee_item_id: int, request: Request, db: Session = Depends(get_db)):
    fi = db.query(models.FeeItem).filter(models.FeeItem.id == fee_item_id).first()
    if not fi:
        raise HTTPException(404, "Fee item not found")
    fi.is_active = not fi.is_active
    audit_from_request(
        request,
        db,
        "UPDATE",
        "FeeItem",
        fee_item_id,
        f"{'Activated' if fi.is_active else 'Deactivated'}: {fi.name}",
    )
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
        notes=payload.notes,
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


@router.delete("/learner/{learner_id}/assign/{assignment_id}", status_code=204)
def remove_assignment_legacy_url(
    learner_id: int,
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    return remove_assignment(learner_id, assignment_id, request, db)
