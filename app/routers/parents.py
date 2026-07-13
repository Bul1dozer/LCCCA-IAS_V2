from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request

router = APIRouter(prefix="/api/parents", tags=["Parents"], dependencies=[Depends(auth.require_admin)])


def _to_detail(parent: models.Parent) -> schemas.ParentDetail:
    learners = []
    for rel in parent.relationships_:
        learners.append(schemas.LearnerMini(
            id=rel.learner.id,
            learner_code=rel.learner.learner_code,
            full_name=rel.learner.full_name,
            grade=rel.learner.grade,
            class_name=rel.learner.class_name,
            relationship_type=rel.relationship_type,
            relationship_id=rel.id,
        ))
    data = schemas.ParentOut.model_validate(parent).model_dump()
    data["learners"] = learners
    return schemas.ParentDetail(**data)


@router.get("", response_model=list[schemas.ParentOut])
def list_parents(
    search: Optional[str] = Query(None, description="Search by name, email or phone"),
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    query = db.query(models.Parent)
    if not include_inactive:
        query = query.filter(models.Parent.is_active == True)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            models.Parent.full_name.ilike(like),
            models.Parent.email.ilike(like),
            models.Parent.phone.ilike(like),
        ))
    return query.order_by(models.Parent.full_name).all()


@router.post("", response_model=schemas.ParentOut, status_code=201)
def create_parent(payload: schemas.ParentCreate, request: Request, db: Session = Depends(get_db)):
    parent = models.Parent(**payload.model_dump())
    db.add(parent)
    db.flush()
    audit_from_request(request, db, "CREATE", "Parent", parent.id,
                       f"Created parent: {parent.full_name}")
    db.commit()
    db.refresh(parent)
    return parent


@router.get("/{parent_id}", response_model=schemas.ParentDetail)
def get_parent(parent_id: int, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(models.Parent.id == parent_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    return _to_detail(parent)


@router.put("/{parent_id}", response_model=schemas.ParentOut)
def update_parent(parent_id: int, payload: schemas.ParentUpdate, request: Request, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id,
        models.Parent.is_active == True,
    ).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    for field, value in payload.model_dump().items():
        setattr(parent, field, value)
    audit_from_request(request, db, "UPDATE", "Parent", parent_id,
                       f"Updated parent: {parent.full_name}")
    db.commit()
    db.refresh(parent)
    return parent


@router.delete("/{parent_id}", status_code=204)
def delete_parent(parent_id: int, request: Request, db: Session = Depends(get_db)):
    """Soft-delete only. Parent contact history must be preserved for audit purposes."""
    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id,
        models.Parent.is_active == True,
    ).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")

    username = request.session.get("username", "system")
    parent.is_active = False
    parent.deleted_at = datetime.utcnow()
    parent.deleted_by = username

    audit_from_request(request, db, "DELETE", "Parent", parent_id,
                       f"Soft-deleted parent: {parent.full_name}")
    db.commit()
    return None


@router.post("/{parent_id}/link-learner", response_model=schemas.ParentDetail, status_code=201)
def link_learner(parent_id: int, payload: schemas.LinkLearnerRequest, request: Request, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id,
        models.Parent.is_active == True,
    ).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    learner = db.query(models.Learner).filter(
        models.Learner.id == payload.learner_id,
        models.Learner.is_active == True,
    ).first()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")

    existing = db.query(models.LearnerParentRelationship).filter(
        models.LearnerParentRelationship.parent_id == parent_id,
        models.LearnerParentRelationship.learner_id == payload.learner_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="This learner is already linked to this parent")

    rel = models.LearnerParentRelationship(
        parent_id=parent_id, learner_id=payload.learner_id, relationship_type=payload.relationship_type,
    )
    db.add(rel)
    audit_from_request(request, db, "CREATE", "LearnerParentRelationship", None,
                       f"Linked learner {learner.learner_code} to parent {parent.full_name}")
    db.commit()
    db.refresh(parent)
    return _to_detail(parent)


@router.delete("/relationships/{relationship_id}", status_code=204)
def unlink_learner(relationship_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Relationship links carry no financial weight themselves, so a hard
    delete is acceptable here — they're not historical accounting records.
    """
    rel = db.query(models.LearnerParentRelationship).filter(
        models.LearnerParentRelationship.id == relationship_id
    ).first()
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    audit_from_request(request, db, "DELETE", "LearnerParentRelationship", relationship_id,
                       f"Unlinked relationship {relationship_id}")
    db.delete(rel)
    db.commit()
    return None
