from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import config
from .db import get_db
from .models import Role, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)
SESSION_ENDED = "Your session was ended because this account signed in on another computer."


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    claims = decode_token(credentials.credentials)
    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = db.scalar(select(User).options(selectinload(User.organization), selectinload(User.roles).selectinload(Role.permissions)).where(User.id == user_id))
    if not user or user.status != "ACTIVE":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or unavailable")
    if config.SINGLE_SESSION:
        if not claims.get("sid") or claims.get("sid") != user.session_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_ENDED)
        now = datetime.now(timezone.utc)
        if not user.session_seen_at or (now - user.session_seen_at).total_seconds() > 30:
            user.session_seen_at = now
            db.commit()
    token_portal = claims.get("portal")
    if token_portal and token_portal not in portals_for(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session is not valid for this account")
    user._session_portal = token_portal or portal_for(user)       # the page this session was opened on (demo limits apply to demo sessions only)
    from . import license_mgmt as lm            # imported here: license_mgmt itself imports this module

    lm.touch_usage(db, user)
    if user.organization is not None:

        blocked = lm.block_reason(user.organization)
        if blocked:                              # a suspension / expiry also ends sessions that are already open
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "LICENSE_BLOCKED", "message": blocked})
    return user


DEMO_BLOCKED_PERMISSIONS = {"organization.update", "organization.create", "user.create", "user.update"}


def portals_for(user: User) -> list[str]:
    """Every login page this account may use: its own portal, plus the other company login page (ERP <-> Demo) when its company is flagged for both."""
    own = portal_for(user)
    other = {"erp": "demo", "demo": "erp"}.get(own)
    return [own, other] if other and user.organization and user.organization.demo_portal else [own]


def portal_for(user: User) -> str:
    """The one portal an account may use, derived from the account itself (never from the client)."""
    if user.is_super_admin:
        return "developer"
    if user.organization and user.organization.is_demo:
        return "demo"
    return "erp"


def has_permission(user: User, code: str) -> bool:
    if user.is_super_admin:
        return True
    return any(p.code == code for role in user.roles for p in role.permissions)


def permission_required(code: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.is_super_admin:
            return user
        if getattr(user, "_session_portal", portal_for(user)) == "demo" and (code.endswith(".delete") or code in DEMO_BLOCKED_PERMISSIONS):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action is not available in the demo environment")
        permissions = {permission.code for role in user.roles for permission in role.permissions}
        if code not in permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return user
    return dependency


def _is_license_admin(user: User) -> bool:
    from .license_mgmt import is_license_admin

    return is_license_admin(user)


def safe_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "organization_id": user.organization_id,
        "organization_name": user.organization.name if user.organization else "eSway ERP 2.0",
        "is_super_admin": user.is_super_admin,
        "roles": [role.name for role in user.roles],
        "permissions": sorted({permission.code for role in user.roles for permission in role.permissions}),
        "portal": getattr(user, "_session_portal", portal_for(user)),
        "license_admin": _is_license_admin(user),
        "license_number": user.organization.license_number if user.organization else None,
        "license_status": (user.organization.status if user.organization else None),
        "license_expiry": user.organization.license_expiry.isoformat() if user.organization and user.organization.license_expiry else None,
        "theme": (user.profile or {}).get("theme"),
        "custom_colors": (user.profile or {}).get("custom_colors"),
    }
