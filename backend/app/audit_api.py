"""Audit reports: the company's own audit trail (sign-ins, what users did, document changes) read from the audit log that every module already writes."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import permission_required
from .db import get_db
from .models import AuditLog, User

router = APIRouter(prefix="/api/audit", tags=["audit"])
DOCUMENT_MODULES = ("BILLING", "SALES", "OPERATIONS", "SHIPMENT", "ACCOUNTS")        # invoices, quotations, bookings, jobs, payments and vouchers


@router.get("")
def audit_trail(kind: str = Query(default="system", pattern="^(login|user|document|system)$"), date_from: date | None = None, date_to: date | None = None, limit: int = Query(default=300, ge=1, le=2000),
                user: User = Depends(permission_required("user.create")), db: Session = Depends(get_db)):
    """kind: login = sign-in history, user = what users did (without sign-ins), document = changes to business documents, system = everything."""
    if user.organization_id is None:
        raise HTTPException(403, "The audit trail is company-scoped")
    q = select(AuditLog).where(AuditLog.organization_id == user.organization_id)
    if kind == "login":
        q = q.where(AuditLog.module == "AUTHENTICATION")
    elif kind == "user":
        q = q.where(AuditLog.module != "AUTHENTICATION")
    elif kind == "document":
        q = q.where(AuditLog.module.in_(DOCUMENT_MODULES))
    if date_from:
        q = q.where(AuditLog.created_at >= date_from)
    if date_to:
        q = q.where(AuditLog.created_at < date.fromordinal(date_to.toordinal() + 1))
    rows = list(db.scalars(q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)))
    names = {u.id: ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.username for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id})))} if rows else {}
    out = []
    for r in rows:
        meta = r.new_value or {}
        out.append({"id": r.id, "when": r.created_at, "user": names.get(r.user_id) or "—", "action": r.action, "module": r.module, "entity": r.entity_type, "entity_id": r.entity_id,
                    "ip": meta.get("ip") or meta.get("client_ip"), "detail": {k: v for k, v in meta.items() if k not in ("ip", "client_ip", "user_agent")}})
    return out
