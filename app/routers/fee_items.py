"""
Dynamic Fee Catalogue & Learner Fee Profile management.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date

from .. import models, auth
from ..database import get_db
from ..audit import audit_from_request
from fastapi import Request

router = APIRouter(prefix="/api/fee-items", tags=["Fee Items"], dependencies=[Depends(auth.require_admin)])


# ---- Fee Catalogue CRUD ----

@router.get("")
def list_fee_items(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(models.FeeItem)
    if active_only:
        q = q.filter(models.FeeItem.is_active == True)
    return [_fee_item_out(fi) for fi in q.order_by(models.FeeItem.sort_order, models.FeeItem.name).all()]


@router.post("", status_code=201)
def create_fee_item(payload: dict, request: Request, db: Session = Depends(get_db)):
    fi = models.FeeItem(
        name=payload["name"],
        description=payload.get("description"),
        category=payload.get("category", "other"),
        frequency=payload["frequency"],
        amount=float(payload.get("amount", 0)),
        is_mandatory=bool(payload.get("is_mandatory", False)),
        is_active=bool(payload.get("is_active", True)),
        effective_from=_parse_date(payload.get("effective_from")),
        effective_to=_parse_date(payload.get("effective_to")),
        applicable_grades=payload.get("applicable_grades") or None,
        applicable_classes=payload.get("applicable_classes") or None,
        sort_order=int(payload.get("sort_order", 0)),
    )
    db.add(fi)
    db.flush()
    audit_from_request(request, db, "CREATE", "FeeItem", fi.id, f"Created fee item: {fi.name}")
    db.commit()
    db.refresh(fi)
    return _fee_item_out(fi)


@router.get("/{fee_item_id}")
def get_fee_item(fee_item_id: int, db: Session = Depends(get_db)):
    fi = _get_or_404(db, fee_item_id)
    return _fee_item_out(fi)


@router.put("/{fee_item_id}")
def update_fee_item(fee_item_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    fi = _get_or_404(db, fee_item_id)
    for field in ["name", "description", "category", "frequency", "is_mandatory", "is_active", "sort_order",
                  "applicable_grades", "applicable_classes"]:
        if field in payload:
            setattr(fi, field, payload[field] if payload[field] != "" else None)
    if "amount" in payload:
        fi.amount = float(payload["amount"])
    if "effective_from" in payload:
        fi.effective_from = _parse_date(payload["effective_from"])
    if "effective_to" in payload:
        fi.effective_to = _parse_date(payload["effective_to"])
    audit_from_request(request, db, "UPDATE", "FeeItem", fi.id, f"Updated fee item: {fi.name}")
    db.commit()
    db.refresh(fi)
    return _fee_item_out(fi)


@router.patch("/{fee_item_id}/toggle")
def toggle_fee_item(fee_item_id: int, request: Request, db: Session = Depends(get_db)):
    fi = _get_or_404(db, fee_item_id)
    fi.is_active = not fi.is_active
    audit_from_request(request, db, "UPDATE", "FeeItem", fi.id,
                       f"{'Activated' if fi.is_active else 'Deactivated'} fee item: {fi.name}")
    db.commit()
    return {"id": fi.id, "is_active": fi.is_active}


@router.delete("/{fee_item_id}", status_code=204)
def delete_fee_item(fee_item_id: int, request: Request, db: Session = Depends(get_db)):
    fi = _get_or_404(db, fee_item_id)
    in_use = db.query(models.LearnerFeeItem).filter(models.LearnerFeeItem.fee_item_id == fee_item_id).count()
    if in_use:
        raise HTTPException(400, detail=f"Cannot delete: fee item is assigned to {in_use} learner(s). Deactivate it instead.")
    audit_from_request(request, db, "DELETE", "FeeItem", fi.id, f"Deleted fee item: {fi.name}")
    db.delete(fi)
    db.commit()
    return None


# ---- Learner Fee Profiles ----

@router.get("/learner/{learner_id}/profile")
def get_learner_profile(learner_id: int, db: Session = Depends(get_db)):
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
    total_monthly = sum(
        (a.custom_amount if a.custom_amount is not None else a.fee_item.amount)
        for a in assignments
        if a.is_active and a.fee_item.frequency == "monthly" and a.fee_item.is_active
    )
    return {
        "learner_id": learner_id,
        "learner_name": learner.full_name,
        "learner_code": learner.learner_code,
        "grade": learner.grade,
        "total_monthly": round(total_monthly, 2),
        "assignments": [_assignment_out(a) for a in assignments],
    }


@router.post("/learner/{learner_id}/assign", status_code=201)
def assign_fee_to_learner(learner_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    fi = _get_or_404(db, payload["fee_item_id"])

    existing = db.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.learner_id == learner_id,
        models.LearnerFeeItem.fee_item_id == payload["fee_item_id"],
    ).first()
    if existing:
        existing.is_active = True
        existing.custom_amount = float(payload["custom_amount"]) if payload.get("custom_amount") not in (None, "") else None
        existing.notes = payload.get("notes")
        db.commit()
        return _assignment_out(existing)

    asgn = models.LearnerFeeItem(
        learner_id=learner_id,
        fee_item_id=payload["fee_item_id"],
        custom_amount=float(payload["custom_amount"]) if payload.get("custom_amount") not in (None, "") else None,
        is_active=True,
        assigned_by=request.session.get("username", "admin"),
        notes=payload.get("notes"),
    )
    db.add(asgn)
    audit_from_request(request, db, "CREATE", "LearnerFeeItem", learner_id,
                       f"Assigned fee '{fi.name}' to learner {learner.learner_code}")
    db.commit()
    db.refresh(asgn)
    return _assignment_out(asgn)


@router.delete("/learner/{learner_id}/assign/{assignment_id}", status_code=204)
def remove_fee_assignment(learner_id: int, assignment_id: int, request: Request, db: Session = Depends(get_db)):
    asgn = db.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.id == assignment_id,
        models.LearnerFeeItem.learner_id == learner_id,
    ).first()
    if not asgn:
        raise HTTPException(404, "Assignment not found")
    audit_from_request(request, db, "DELETE", "LearnerFeeItem", assignment_id,
                       f"Removed fee assignment {assignment_id} from learner {learner_id}")
    db.delete(asgn)
    db.commit()
    return None


@router.post("/learner/{learner_id}/auto-assign")
def auto_assign(learner_id: int, request: Request, db: Session = Depends(get_db)):
    from ..fee_engine import auto_assign_mandatory_fees
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    count = auto_assign_mandatory_fees(db, learner, assigned_by=request.session.get("username", "admin"))
    audit_from_request(request, db, "CREATE", "LearnerFeeItem", learner_id,
                       f"Auto-assigned {count} mandatory fees to learner {learner.learner_code}")
    db.commit()
    return {"assigned": count}


# ---- Transport Routes ----

@router.get("/transport/routes")
def list_routes(db: Session = Depends(get_db)):
    return [
        {"id": r.id, "name": r.name, "description": r.description,
         "monthly_fee": r.monthly_fee, "is_active": r.is_active}
        for r in db.query(models.TransportRoute).order_by(models.TransportRoute.name).all()
    ]


@router.post("/transport/routes", status_code=201)
def create_route(payload: dict, request: Request, db: Session = Depends(get_db)):
    route = models.TransportRoute(
        name=payload["name"],
        description=payload.get("description"),
        monthly_fee=float(payload.get("monthly_fee", 0)),
        is_active=True,
    )
    db.add(route)
    audit_from_request(request, db, "CREATE", "TransportRoute", None, f"Created route: {route.name}")
    db.commit()
    db.refresh(route)
    return {"id": route.id, "name": route.name, "monthly_fee": route.monthly_fee}


@router.put("/transport/routes/{route_id}")
def update_route(route_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    route = db.query(models.TransportRoute).filter(models.TransportRoute.id == route_id).first()
    if not route:
        raise HTTPException(404, "Route not found")
    for field in ["name", "description", "is_active"]:
        if field in payload:
            setattr(route, field, payload[field])
    if "monthly_fee" in payload:
        route.monthly_fee = float(payload["monthly_fee"])
    audit_from_request(request, db, "UPDATE", "TransportRoute", route_id, f"Updated route: {route.name}")
    db.commit()
    db.refresh(route)
    return {"id": route.id, "name": route.name, "monthly_fee": route.monthly_fee}


# ---- Helpers ----

def _get_or_404(db, fee_item_id):
    fi = db.query(models.FeeItem).filter(models.FeeItem.id == fee_item_id).first()
    if not fi:
        raise HTTPException(404, "Fee item not found")
    return fi


def _parse_date(v):
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except Exception:
        return None


def _fee_item_out(fi: models.FeeItem) -> dict:
    return {
        "id": fi.id, "name": fi.name, "description": fi.description,
        "category": fi.category, "frequency": fi.frequency,
        "amount": fi.amount, "is_mandatory": fi.is_mandatory, "is_active": fi.is_active,
        "effective_from": str(fi.effective_from) if fi.effective_from else None,
        "effective_to": str(fi.effective_to) if fi.effective_to else None,
        "applicable_grades": fi.applicable_grades, "applicable_classes": fi.applicable_classes,
        "sort_order": fi.sort_order,
        "created_at": fi.created_at.isoformat() if fi.created_at else None,
    }


def _assignment_out(a: models.LearnerFeeItem) -> dict:
    fi = a.fee_item
    return {
        "id": a.id, "learner_id": a.learner_id, "fee_item_id": a.fee_item_id,
        "fee_name": fi.name if fi else None,
        "fee_category": fi.category if fi else None,
        "fee_frequency": fi.frequency if fi else None,
        "base_amount": fi.amount if fi else 0,
        "custom_amount": a.custom_amount,
        "effective_amount": a.custom_amount if a.custom_amount is not None else (fi.amount if fi else 0),
        "is_active": a.is_active, "is_mandatory": fi.is_mandatory if fi else False,
        "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
        "notes": a.notes,
    }
