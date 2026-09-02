from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import Customer, User
from .schemas_auth import CustomerCreate, CustomerUpdate

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _require_tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer Master is tenant-scoped; Super Admin does not manage customer records directly")
    return user.organization_id


@router.get("")
def list_customers(user: User = Depends(permission_required("customer.read")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    return db.scalars(select(Customer).where(Customer.organization_id == organization_id).order_by(Customer.name)).all()


@router.post("", status_code=201)
def create_customer(payload: CustomerCreate, user: User = Depends(permission_required("customer.create")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    customer = Customer(organization_id=organization_id, created_by=user.id, **payload.model_dump())
    db.add(customer)
    try:
        db.flush()
        record_audit(db, "CUSTOMER_CREATED", "CUSTOMER", user, organization_id, "Customer", str(customer.id), {"code": customer.code, "name": customer.name})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A customer with this code already exists")
    db.refresh(customer)
    return customer


@router.get("/{customer_id}")
def get_customer(customer_id: int, user: User = Depends(permission_required("customer.read")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == organization_id))
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.patch("/{customer_id}")
def update_customer(customer_id: int, payload: CustomerUpdate, user: User = Depends(permission_required("customer.update")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == organization_id))
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(customer, key, value)
    record_audit(db, "CUSTOMER_UPDATED", "CUSTOMER", user, organization_id, "Customer", str(customer.id), changes)
    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}", status_code=204)
def delete_customer(customer_id: int, user: User = Depends(permission_required("customer.delete")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == organization_id))
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    record_audit(db, "CUSTOMER_DELETED", "CUSTOMER", user, organization_id, "Customer", str(customer.id), {"code": customer.code, "name": customer.name})
    db.delete(customer)
    db.commit()
    return None
