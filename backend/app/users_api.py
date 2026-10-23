import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import User
from .roles import ROLE_LABELS, ensure_role
from .security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])

PROFILE_KEYS = re.compile(r"^[a-z0-9_]{1,60}$")


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)       # the user chooses the password; no length rule
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(default="", max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    role: str = "OPERATIONS"
    profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile")
    @classmethod
    def _profile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_profile(value)


class UserPatch(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    role: str | None = None
    profile: dict[str, Any] | None = None
    new_password: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("profile")
    @classmethod
    def _profile(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _clean_profile(value)


def _clean_profile(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > 60:
        raise ValueError("too many profile fields")
    for key, item in value.items():
        if not PROFILE_KEYS.match(key):
            raise ValueError(f"invalid profile field: {key[:30]}")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("profile values must be scalars")
        if isinstance(item, str) and len(item) > 500:
            raise ValueError("profile value too long")
    return value


def _out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "status": user.status,
        "roles": [role.name for role in user.roles],
        "role_labels": [ROLE_LABELS.get(role.name, role.name) for role in user.roles],
        "profile": user.profile or {},
        "last_login_at": user.last_login_at,
        "locked": bool(user.locked_until),
    }


def _tenant(actor: User) -> int:
    if actor.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Users are tenant-scoped; use the Organizations screen for platform accounts")
    return actor.organization_id


def _get(db: Session, org_id: int, user_id: int) -> User:
    user = db.scalar(select(User).options(selectinload(User.roles)).where(User.id == user_id, User.organization_id == org_id, User.is_super_admin.is_(False)))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/roles")
def list_roles(actor: User = Depends(permission_required("user.read"))):
    return [{"key": key, "label": label} for key, label in ROLE_LABELS.items()]


@router.get("")
def list_users(actor: User = Depends(permission_required("user.read")), db: Session = Depends(get_db)):
    org_id = _tenant(actor)
    users = db.scalars(select(User).options(selectinload(User.roles)).where(User.organization_id == org_id, User.is_super_admin.is_(False)).order_by(User.id))
    return [_out(u) for u in users]


@router.post("", status_code=201)
def create_user(payload: UserCreate, actor: User = Depends(permission_required("user.create")), db: Session = Depends(get_db)):
    org_id = _tenant(actor)
    if db.scalar(select(User.id).where((func.lower(User.email) == payload.email.lower()) | (func.lower(User.username) == payload.username.lower()))):
        raise HTTPException(status_code=409, detail="A user with this username or email already exists")
    role = ensure_role(db, org_id, payload.role)
    user = User(organization_id=org_id, username=payload.username.lower(), email=payload.email.lower(), password_hash=hash_password(payload.password), first_name=payload.first_name, last_name=payload.last_name, phone=payload.phone, status="ACTIVE", profile=payload.profile)
    user.roles.append(role)
    db.add(user)
    try:
        db.flush()
        record_audit(db, "USER_CREATED", "USER", actor, org_id, "User", str(user.id), {"username": user.username, "role": payload.role})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with this username or email already exists")
    return _out(db.scalar(select(User).options(selectinload(User.roles)).where(User.id == user.id)))


@router.get("/{user_id}")
def get_user(user_id: int, actor: User = Depends(permission_required("user.read")), db: Session = Depends(get_db)):
    return _out(_get(db, _tenant(actor), user_id))


@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserPatch, actor: User = Depends(permission_required("user.update")), db: Session = Depends(get_db)):
    org_id = _tenant(actor)
    user = _get(db, org_id, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("status") == "INACTIVE" and user.id == actor.id:
        raise HTTPException(status_code=422, detail="You cannot deactivate your own account")
    if "role" in changes and changes["role"] is not None:
        if user.id == actor.id and changes["role"] != "ERP_ADMIN" and "ERP_ADMIN" in [r.name for r in user.roles]:
            raise HTTPException(status_code=422, detail="You cannot remove your own administrator role")
        user.roles = [ensure_role(db, org_id, changes["role"])]
    audit: dict[str, Any] = {}
    for key in ("first_name", "last_name", "phone", "status", "profile"):
        if key in changes and changes[key] is not None:
            setattr(user, key, changes[key])
            if key != "profile":
                audit[key] = changes[key]
    if changes.get("new_password"):
        user.password_hash = hash_password(changes["new_password"])
        user.failed_login_count = 0
        user.locked_until = None
        record_audit(db, "PASSWORD_RESET", "USER", actor, org_id, "User", str(user.id))
    if "role" in changes:
        audit["role"] = changes["role"]
    record_audit(db, "USER_UPDATED", "USER", actor, org_id, "User", str(user.id), audit)
    db.commit()
    return _out(_get(db, org_id, user_id))
