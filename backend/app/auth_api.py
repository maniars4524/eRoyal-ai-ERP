import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from . import config, std_masters
from .auth import get_current_user, permission_required, portal_for, portals_for, safe_user
from .db import get_db
from .licensing import next_license_number
from .models import AuditLog, Organization, Permission, Role, User
from .roles import ensure_role
from .schemas_auth import LoginRequest, LoginResponse, OrganizationAdminCreate, OrganizationUpdate, SafeUser
from .security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["authentication"])
platform_router = APIRouter(prefix="/api/organizations", tags=["organizations"])


def record_audit(db: Session, action: str, module: str, user: User | None = None, organization_id: int | None = None, entity_type: str | None = None, entity_id: str | None = None, new_value: dict | None = None):
    db.add(AuditLog(action=action, module=module, user_id=user.id if user else None, organization_id=organization_id, entity_type=entity_type, entity_id=entity_id, new_value=new_value))




MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
PORTAL_LABELS = {"developer": "Developer / System Administrator", "erp": "ERP", "demo": "Demo"}


def _public_only_message() -> str:
    names = " and ".join(PORTAL_LABELS[p] for p in sorted(config.PUBLIC_PORTALS))
    return f"This is a public server. Only the {names} portal is available here."


def _request_meta(request: Request) -> dict:
    return {"ip": request.client.host if request.client else None, "user_agent": (request.headers.get("user-agent") or "")[:200]}


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    meta = _request_meta(request)
    if config.PUBLIC_PORTALS and payload.portal is not None and payload.portal not in config.PUBLIC_PORTALS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_public_only_message())
    try:
        identifier = payload.identifier.strip()
        user = db.scalar(select(User).options(selectinload(User.organization), selectinload(User.roles).selectinload(Role.permissions)).where((func.lower(User.email) == identifier.lower()) | (func.lower(User.username) == identifier.lower())))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Authentication service unavailable: PostgreSQL is not connected")
    now = datetime.now(timezone.utc)
    portal = portal_for(user) if user else None
    if user and payload.portal in portals_for(user):
        portal = payload.portal           # the page the user is signing in on, when the account is allowed on it
    shared_demo = bool(config.PUBLIC_PORTALS) and portal == "demo"  # public shared demo account is never locked
    if user and not shared_demo and user.locked_until and user.locked_until > now:
        record_audit(db, "LOGIN_BLOCKED_LOCKED", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), meta)
        db.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Account temporarily locked after repeated failed sign-ins. Try again later.")
    expected_license = None
    if user:
        expected_license = user.organization.license_number if user.organization else db.scalar(select(Organization.license_number).where(Organization.code == "RTS-PLATFORM"))
    license_ok = True
    if user and expected_license and portal == "erp":
        license_ok = (payload.license_number or "").strip().upper() == expected_license.upper()
    elif user and expected_license and portal == "demo" and (payload.license_number or "").strip():
        license_ok = (payload.license_number or "").strip().upper() == expected_license.upper()      # the Demo page shows the license it belongs to; if one is typed it must be the account's own
    if not user or user.status != "ACTIVE" or not verify_password(payload.password, user.password_hash) or not license_ok:
        if user and not shared_demo:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_count = 0
                record_audit(db, "ACCOUNT_LOCKED", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), meta)
            record_audit(db, "LOGIN_FAILED", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), meta)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if config.PUBLIC_PORTALS and portal not in config.PUBLIC_PORTALS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_public_only_message())
    if payload.portal and payload.portal != portal:
        record_audit(db, "LOGIN_WRONG_PORTAL", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), {**meta, "requested": payload.portal, "allowed": portal})
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"This account cannot sign in to the {PORTAL_LABELS[payload.portal]} portal. Use the {PORTAL_LABELS[portal]} portal instead.")
    from . import license_mgmt as lm        # imported here: license_mgmt itself imports this module

    blocked = lm.block_reason(user.organization) if user.organization else None
    if blocked:
        record_audit(db, "LOGIN_BLOCKED_LICENSE", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), {**meta, "status": lm.effective_status(user.organization)})
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "LICENSE_BLOCKED", "message": blocked})
    sid = None
    if config.SINGLE_SESSION:
        live = user.session_id and user.session_seen_at and (now - user.session_seen_at).total_seconds() < config.SESSION_ACTIVE_MINUTES * 60
        if live and not payload.replace_session:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "ALREADY_SIGNED_IN", "message": "This account is already signed in on another computer. Continuing will sign that computer out.", "ip": user.session_ip, "since": user.session_started_at.isoformat() if user.session_started_at else None})
        if live:
            record_audit(db, "SESSION_REPLACED", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), {**meta, "previous_ip": user.session_ip})
        org = user.organization
        if org is not None and org.max_sessions is not None and not live and lm.live_session_count(db, org.id, config.SESSION_ACTIVE_MINUTES) >= org.max_sessions:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=f"The license allows {org.max_sessions} user(s) signed in at the same time and all of them are in use. Try again later.")
        sid = uuid.uuid4().hex
        user.session_id, user.session_ip, user.session_started_at, user.session_seen_at = sid, meta["ip"], now, now
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    record_audit(db, "LOGIN", "AUTHENTICATION", user, user.organization_id, "User", str(user.id), {**meta, "portal": portal})
    db.commit()
    user._session_portal = portal
    return {"access_token": create_token(user.id, user.is_super_admin, user.organization_id, portal, sid), "user": safe_user(user)}


THEMES = ("navy", "brown", "charcoal", "forest", "purple", "teal", "burgundy", "midnight")


class Preferences(BaseModel):
    theme: str | None = Field(default=None, pattern="^(navy|brown|charcoal|forest|purple|teal|burgundy|midnight)$")
    custom_colors: dict[str, str] | None = None            # the user's own three colours: bg (page background), title (header and titles), accent (buttons and highlights); an empty dict clears them
    clear_custom: bool = False


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@router.put("/preferences")
def save_preferences(body: Preferences, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The user's own display preferences (the colour theme and optional own colours). It follows the account to every computer."""
    prof = dict(user.profile or {})
    if body.theme:
        prof["theme"] = body.theme
    if body.clear_custom:
        prof.pop("custom_colors", None)
    elif body.custom_colors is not None:
        colors = {k: v.upper() for k, v in body.custom_colors.items() if k in ("bg", "title", "accent")}
        if any(not _HEX.match(v) for v in colors.values()) or set(colors) != {"bg", "title", "accent"}:
            raise HTTPException(status_code=422, detail="Give the three colours (background, titles, accent) as #RRGGBB")
        prof["custom_colors"] = colors
    user.profile = prof
    db.commit()
    return {"theme": prof.get("theme"), "custom_colors": prof.get("custom_colors")}


@router.get("/portal-info")
def portal_info(portal: str = "demo", db: Session = Depends(get_db)):
    """What a login page needs before anyone signs in: the license the Demo page belongs to (shown above the user name). A license number is an identifier, not a secret."""
    if portal != "demo":
        return {"license": None}
    org = db.scalar(select(Organization).where(Organization.demo_portal.is_(True), Organization.status == "ACTIVE").order_by(Organization.id)) or \
        db.scalar(select(Organization).where(Organization.is_demo.is_(True), Organization.status == "ACTIVE").order_by(Organization.id))
    return {"license": org.license_number if org else None}


_LICENSE_LOOKUPS: dict[str, list[float]] = {}


@router.get("/license-company")
def license_company(license: str = "", request: Request = None, db: Session = Depends(get_db)):
    """The login card shows the company name once a license number is typed. Only the name of an active company comes back (a license number is an identifier, not a secret); lookups are limited per address."""
    import time

    now = time.time()
    ip = (request.client.host if request and request.client else "?")
    hits = [t for t in _LICENSE_LOOKUPS.get(ip, []) if now - t < 60]
    if len(hits) >= 30:
        raise HTTPException(status_code=429, detail="Too many lookups, try again in a minute")
    _LICENSE_LOOKUPS[ip] = hits + [now]
    lic = (license or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}\d{3,8}", lic):
        return {"name": None}
    org = db.scalar(select(Organization).where(Organization.license_number == lic, Organization.status == "ACTIVE"))
    return {"name": (org.legal_name or org.name) if org and org.id and not getattr(org, "is_platform", False) and lic != "RTS0001" else None}


@router.get("/me", response_model=SafeUser)
def me(user: User = Depends(get_current_user)):
    return safe_user(user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.session_id = user.session_ip = user.session_started_at = user.session_seen_at = None
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


def create_company(db: Session, payload: OrganizationAdminCreate, actor: User, commit: bool = True) -> tuple[Organization, User]:
    """Create a company with its first administrator and the standard masters (shared by the developer portal and License Management).
    commit=False leaves the transaction open so the caller can add more to it and commit once (all or nothing)."""
    if db.scalar(select(Organization).where(Organization.code == payload.code)) or db.scalar(select(User).where((User.email == payload.admin_email) | (User.username == payload.admin_username))):
        raise HTTPException(status_code=409, detail="Organization code or administrator identity already exists")
    organization = Organization(code=payload.code, license_number=next_license_number(db), name=payload.name, legal_name=payload.legal_name, country=payload.country, status="ACTIVE")
    db.add(organization)
    db.flush()
    admin_role = ensure_role(db, organization.id, "ERP_ADMIN")
    if not admin_role.permissions:
        raise HTTPException(status_code=500, detail="Foundation permissions have not been seeded")
    admin = User(email=payload.admin_email, username=payload.admin_username.lower(), password_hash=hash_password(payload.admin_password), first_name=payload.admin_first_name, last_name=payload.admin_last_name, status="ACTIVE", is_super_admin=False)
    organization.users.append(admin)
    organization.created_by = actor.id
    std_masters.seed_org(db, organization.id, user_id=actor.id)
    admin.roles.append(admin_role)
    try:
        db.flush()
        from .reference_masters import provision_new_company

        provision_new_company(db, organization)        # Airline, Sea Line, Charge and HSN masters start from the owner company's lists
        record_audit(db, "ORGANIZATION_CREATED", "ORGANIZATION", actor, organization.id, "Organization", str(organization.id), {"code": organization.code, "name": organization.name})
        record_audit(db, "USER_CREATED", "USER", actor, organization.id, "User", str(admin.id), {"username": admin.username, "role": "ERP_ADMIN"})
        if commit:
            db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Organization or administrator already exists")
    return organization, admin


@platform_router.post("", status_code=201)
def create_organization(payload: OrganizationAdminCreate, user: User = Depends(permission_required("organization.create")), db: Session = Depends(get_db)):
    organization, admin = create_company(db, payload, user)
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
