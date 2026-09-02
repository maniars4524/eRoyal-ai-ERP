from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from .auth import get_current_user, permission_required, safe_user
from .db import get_db
from .licensing import next_license_number
from .models import AuditLog, Organization, Permission, Role, User
from .schemas_auth import LoginRequest, LoginResponse, OrganizationAdminCreate, OrganizationUpdate, SafeUser
from .security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["authentication"])
platform_router = APIRouter(prefix="/api/organizations", tags=["organizations"])


def record_audit(db: Session, action: str, module: str, user: User | None = None, organization_id: int | None = None, entity_type: str | None = None, entity_id: str | None = None, new_value: dict | None = None):
    db.add(AuditLog(action=action, module=module, user_id=user.id if user else None, organization_id=organization_id, entity_type=entity_type, entity_id=entity_id, new_value=new_value))




@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        identifier = payload.identifier.strip()
        user = db.scalar(select(User).options(selectinload(User.organization), selectinload(User.roles).selectinload(Role.permissions)).where((func.lower(User.email) == identifier.lower()) | (func.lower(User.username) == identifier.lower())))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Authentication service unavailable: PostgreSQL is not connected")
    if not user or user.status != "ACTIVE" or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    expected_license = user.organization.license_number if user.organization else db.scalar(select(Organization.license_number).where(Organization.code == "RTS-PLATFORM"))
    if expected_license and payload.license_number.strip().upper() != expected_license.upper():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    record_audit(db, "LOGIN", "AUTHENTICATION", user, user.organization_id, "User", str(user.id))
    db.commit()
    return {"access_token": create_token(user.id, user.is_super_admin, user.organization_id), "user": safe_user(user)}


@router.get("/me", response_model=SafeUser)
def me(user: User = Depends(get_current_user)):
    return safe_user(user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    record_audit(db, "LOGOUT", "AUTHENTICATION", user, user.organization_id, "User", str(user.id))
    db.commit()
    return {"status": "ok"}


@platform_router.get("")
def list_organizations(user: User = Depends(permission_required("organization.read")), db: Session = Depends(get_db)):
    if not user.is_super_admin:
        query = select(Organization).where(Organization.id == user.organization_id)
    else:
        query = select(Organization).order_by(Organization.name)
    return db.scalars(query).all()


@platform_router.post("", status_code=201)
def create_organization(payload: OrganizationAdminCreate, user: User = Depends(permission_required("organization.create")), db: Session = Depends(get_db)):
    if db.scalar(select(Organization).where(Organization.code == payload.code)) or db.scalar(select(User).where((User.email == payload.admin_email) | (User.username == payload.admin_username))):
        raise HTTPException(status_code=409, detail="Organization code or administrator identity already exists")
    organization = Organization(code=payload.code, license_number=next_license_number(db), name=payload.name, legal_name=payload.legal_name, country=payload.country, status="ACTIVE")
    admin_role = Role(name="ERP_ADMIN", description="Organization administrator", status="ACTIVE")
    foundation_codes = ["organization.read", "organization.update", "user.read", "user.create"]
    admin_role.permissions = db.scalars(select(Permission).where(Permission.code.in_(foundation_codes))).all()
    if len(admin_role.permissions) != len(foundation_codes):
        raise HTTPException(status_code=500, detail="Foundation permissions have not been seeded")
    admin = User(email=payload.admin_email, username=payload.admin_username.lower(), password_hash=hash_password(payload.admin_password), first_name=payload.admin_first_name, last_name=payload.admin_last_name, status="ACTIVE", is_super_admin=False)
    organization.users.append(admin)
    organization.created_by = user.id
    admin.roles.append(admin_role)
    db.add(organization)
    try:
        db.flush()
        record_audit(db, "ORGANIZATION_CREATED", "ORGANIZATION", user, organization.id, "Organization", str(organization.id), {"code": organization.code, "name": organization.name})
        record_audit(db, "USER_CREATED", "USER", user, organization.id, "User", str(admin.id), {"username": admin.username, "role": "ERP_ADMIN"})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Organization or administrator already exists")
    return {"organization": organization, "admin": safe_user(admin)}


@platform_router.get("/{organization_id}")
def get_organization(organization_id: int, user: User = Depends(permission_required("organization.read")), db: Session = Depends(get_db)):
    if not user.is_super_admin and user.organization_id != organization_id:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    organization = db.get(Organization, organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


@platform_router.patch("/{organization_id}")
def update_organization(organization_id: int, payload: OrganizationUpdate, user: User = Depends(permission_required("organization.update")), db: Session = Depends(get_db)):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Platform organization management requires Super Admin")
    organization = db.get(Organization, organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(organization, key, value)
    record_audit(db, "ORGANIZATION_UPDATED", "ORGANIZATION", user, organization.id, "Organization", str(organization.id), changes)
    db.commit()
    db.refresh(organization)
    return organization
