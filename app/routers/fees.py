from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/api/fee-structures", tags=["Fee Structures"], dependencies=[Depends(auth.require_admin)])


def _calc_total(payload) -> Decimal:
    return (
        payload.tuition_fee + payload.development_fee
        + payload.transport_fee + payload.misc_charges
    ).quantize(Decimal("0.01"))


@router.get("", response_model=list[schemas.FeeStructureOut])
def list_fee_structures(db: Session = Depends(get_db)):
    return db.query(models.FeeStructure).order_by(models.FeeStructure.created_at.desc()).all()


@router.post("", response_model=schemas.FeeStructureOut, status_code=201)
def create_fee_structure(payload: schemas.FeeStructureCreate, db: Session = Depends(get_db)):
    fee = models.FeeStructure(**payload.model_dump(), total=_calc_total(payload))
    db.add(fee)
    db.commit()
    db.refresh(fee)
    return fee


@router.get("/{fee_id}", response_model=schemas.FeeStructureOut)
def get_fee_structure(fee_id: int, db: Session = Depends(get_db)):
    fee = db.query(models.FeeStructure).filter(models.FeeStructure.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee structure not found")
    return fee


@router.put("/{fee_id}", response_model=schemas.FeeStructureOut)
def update_fee_structure(fee_id: int, payload: schemas.FeeStructureUpdate, db: Session = Depends(get_db)):
    fee = db.query(models.FeeStructure).filter(models.FeeStructure.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee structure not found")
    for field, value in payload.model_dump().items():
        setattr(fee, field, value)
    fee.total = _calc_total(payload)
    db.commit()
    db.refresh(fee)
    return fee


@router.delete("/{fee_id}", status_code=204)
def delete_fee_structure(fee_id: int, db: Session = Depends(get_db)):
    fee = db.query(models.FeeStructure).filter(models.FeeStructure.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee structure not found")
    in_use = db.query(models.Invoice).filter(models.Invoice.fee_structure_id == fee_id).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Cannot delete a fee structure that is referenced by invoices")
    db.delete(fee)
    db.commit()
    return None
