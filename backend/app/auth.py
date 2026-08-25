from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .models import Role, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


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
    return user


def permission_required(code: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.is_super_admin:
            return user
        permissions = {permission.code for role in user.roles for permission in role.permissions}
        if code not in permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return user
    return dependency


def safe_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "organization_id": user.organization_id,
        "organization_name": user.organization.name if user.organization else "eRoyal",
        "is_super_admin": user.is_super_admin,
        "roles": [role.name for role in user.roles],
        "permissions": sorted({permission.code for role in user.roles for permission in role.permissions}),
    }
