"""Chart of Accounts API: the group / ledger tree, new group, new G/L, edit, move, activate / deactivate, search and export."""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import coa, gl
from .auth_api import record_audit
from .auth import permission_required
from .db import get_db
from .models import AccGroup, AccLedger, User

router = APIRouter(prefix="/api/coa", tags=["chart-of-accounts"])


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "The chart of accounts is tenant-scoped")
    return user.organization_id


class GroupIn(BaseModel):
    parent_id: int
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=12, pattern=r"^[A-Za-z]{1,12}$")


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


class LedgerIn(BaseModel):
    group_id: int
    name: str = Field(min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=120)
    currency: str = Field(default="INR", max_length=8)
    opening_balance: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    opening_side: str = Field(default="Dr", pattern="^(Dr|Cr)$")


class LedgerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=120)
    currency: str | None = Field(default=None, max_length=8)
    opening_balance: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
    opening_side: str | None = Field(default=None, pattern="^(Dr|Cr)$")
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


class MoveIn(BaseModel):
    kind: str = Field(pattern="^(group|ledger)$")
    id: int
    to_group_id: int


def _led(l: AccLedger, path: str = "") -> dict:
    return {"id": l.id, "kind": "ledger", "group_id": l.group_id, "code": l.code, "name": l.name, "alias": l.alias, "currency": l.currency, "opening_balance": str(l.opening_balance or 0), "opening_side": l.opening_side,
            "control": l.control, "system": bool(l.system_key), "source_type": l.source_type, "source_id": l.source_id, "source_role": l.source_role, "status": l.status, "path": path}


def _build(db: Session, org_id: int) -> tuple[list[dict], dict, dict]:
    groups = list(db.scalars(select(AccGroup).where(AccGroup.organization_id == org_id).order_by(AccGroup.sort_order, AccGroup.id)))
    ledgers = list(db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id).order_by(AccLedger.code)))
    by_id = {g.id: g for g in groups}
    kids: dict = {}
    for g in groups:
        kids.setdefault(g.parent_id, []).append(g)
    leds: dict = {}
    for l in ledgers:
        leds.setdefault(l.group_id, []).append(l)

    def path_of(g):
        names = []
        while g:
            names.insert(0, g.name)
            g = by_id.get(g.parent_id)
        return " > ".join(names)

    def node(g, level):
        return {"id": g.id, "kind": "group", "level": level, "name": g.name, "code": g.code, "nature": g.nature, "system": g.is_system, "status": g.status, "parent_id": g.parent_id, "path": path_of(g),
                "groups": [node(k, level + 1) for k in kids.get(g.id, [])], "ledgers": [_led(l, path_of(g)) for l in leds.get(g.id, [])]}

    return [node(g, 1) for g in kids.get(None, [])], by_id, leds


def _count(n: dict) -> int:
    return len(n["ledgers"]) + sum(_count(k) for k in n["groups"])


@router.get("/tree")
def tree(user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    coa.ensure_tree(db, org_id)
    db.commit()
    if not db.scalar(select(func.count()).select_from(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.source_type.isnot(None))):
        coa.sync_all(db, org_id, user.id)   # a company that had masters before the chart existed gets their ledgers once
    db.commit()
    roots, _, _ = _build(db, org_id)

    def tot(n):
        n["ledger_count"] = _count(n)
        for k in n["groups"]:
            tot(k)
    for r in roots:
        tot(r)
    return {"groups": roots}


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=100), user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    coa.ensure_tree(db, org_id)
    db.commit()
    like = f"%{q.strip()}%"
    out = []
    for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id, (AccLedger.name.ilike(like)) | (AccLedger.code.ilike(like)) | (AccLedger.alias.ilike(like))).order_by(AccLedger.code).limit(100)):
        g = db.get(AccGroup, l.group_id)
        out.append(_led(l, " > ".join(x.name for x in coa.group_path(db, g))))
    for g in db.scalars(select(AccGroup).where(AccGroup.organization_id == org_id, AccGroup.name.ilike(like)).order_by(AccGroup.name).limit(50)):
        out.append({"id": g.id, "kind": "group", "code": g.code, "name": g.name, "status": g.status, "path": " > ".join(x.name for x in coa.group_path(db, g))})
    return out


@router.get("/ledgers")
def ledgers(group_id: int | None = None, source_type: str | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Flat ledger list for pickers (journal voucher, opening balances) and reports."""
    org_id = _tenant(user)
    coa.ensure_tree(db, org_id)
    db.commit()
    q = select(AccLedger).where(AccLedger.organization_id == org_id)
    if group_id:
        q = q.where(AccLedger.group_id == group_id)
    if source_type:
        q = q.where(AccLedger.source_type == source_type)
    groups = {g.id: g for g in db.scalars(select(AccGroup).where(AccGroup.organization_id == org_id))}

    def path(gid):
        names = []
        g = groups.get(gid)
        while g:
            names.insert(0, g.name)
            g = groups.get(g.parent_id)
        return " > ".join(names)
    return [_led(l, path(l.group_id)) for l in db.scalars(q.order_by(AccLedger.code))]


def _group(db: Session, org_id: int, gid: int) -> AccGroup:
    g = db.get(AccGroup, gid)
    if g is None or g.organization_id != org_id:
        raise HTTPException(404, "Group not found")
    return g


def _ledger(db: Session, org_id: int, lid: int) -> AccLedger:
    l = db.get(AccLedger, lid)
    if l is None or l.organization_id != org_id:
        raise HTTPException(404, "Ledger not found")
    return l


@router.post("/groups", status_code=201)
def new_group(body: GroupIn, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    parent = _group(db, org_id, body.parent_id)
    code = (body.code or parent.code).upper()
    g = AccGroup(organization_id=org_id, parent_id=parent.id, name=body.name.strip().upper(), code=code, nature=parent.nature, is_system=False, sort_order=99)
    db.add(g)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A group with this name already exists under the selected parent")
    record_audit(db, "ACC_GROUP_CREATED", "ACCOUNTS", user, org_id, "AccGroup", str(g.id), {"name": g.name, "parent": parent.name})
    db.commit()
    return {"id": g.id, "name": g.name, "code": g.code}


@router.patch("/groups/{gid}")
def edit_group(gid: int, body: GroupPatch, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    g = _group(db, org_id, gid)
    if g.is_system and body.name and body.name.strip().upper() != g.name:
        raise HTTPException(409, "A standard group cannot be renamed. Add a sub group instead.")
    if body.name:
        g.name = body.name.strip().upper()
    if body.status:
        if g.is_system and body.status == "INACTIVE" and g.parent_id is None:
            raise HTTPException(409, "A main group cannot be deactivated")
        g.status = body.status
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A group with this name already exists under the same parent")
    record_audit(db, "ACC_GROUP_UPDATED", "ACCOUNTS", user, org_id, "AccGroup", str(g.id), body.model_dump(exclude_none=True))
    db.commit()
    return {"id": g.id, "name": g.name, "status": g.status}


@router.post("/ledgers", status_code=201)
def new_ledger(body: LedgerIn, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    g = _group(db, org_id, body.group_id)
    if g.status != "ACTIVE":
        raise HTTPException(409, "The group is inactive")
    if g.parent_id is None:
        raise HTTPException(409, "A ledger cannot sit directly under a main group. Pick a sub group.")
    if db.scalar(select(AccLedger.id).where(AccLedger.organization_id == org_id, AccLedger.group_id == g.id, func.lower(AccLedger.name) == body.name.strip().lower())):
        raise HTTPException(409, "A ledger with this name already exists in the group")
    led = coa._make_ledger(db, org_id, g, body.name, alias=body.alias, user_id=user.id, currency=body.currency.upper())
    led.opening_balance, led.opening_side = body.opening_balance, body.opening_side
    record_audit(db, "ACC_LEDGER_CREATED", "ACCOUNTS", user, org_id, "AccLedger", str(led.id), {"code": led.code, "name": led.name, "group": g.name})
    db.commit()
    return _led(led)


@router.patch("/ledgers/{lid}")
def edit_ledger(lid: int, body: LedgerPatch, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    led = _ledger(db, org_id, lid)
    ch = body.model_dump(exclude_unset=True)
    if ("name" in ch and ch["name"].strip() != led.name) and (led.system_key or led.source_type):
        raise HTTPException(409, "This ledger is created from a master or by the system. Rename it in the master.")
    if "status" in ch and ch["status"] == "INACTIVE" and led.system_key:
        raise HTTPException(409, "A system ledger cannot be deactivated")
    changes_opening = (ch.get("opening_balance") is not None and Decimal(ch["opening_balance"]) != Decimal(led.opening_balance or 0)) or (ch.get("opening_side") not in (None, led.opening_side))
    if changes_opening and gl.locked_until(db, org_id):
        raise HTTPException(409, "A financial year is closed, so opening balances can no longer be changed. Reopen the years first")
    for k, v in ch.items():
        if v is not None:
            setattr(led, k, v.strip() if isinstance(v, str) else v)
    record_audit(db, "ACC_LEDGER_UPDATED", "ACCOUNTS", user, org_id, "AccLedger", str(led.id), {k: str(v) for k, v in ch.items()})
    db.commit()
    return _led(led)


@router.post("/move")
def move(body: MoveIn, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    """Move a ledger to another group, or a group under another parent. A move never leaves the group's nature (ASSETS stays within ASSETS)."""
    org_id = _tenant(user)
    target = _group(db, org_id, body.to_group_id)
    if body.kind == "ledger":
        led = _ledger(db, org_id, body.id)
        if target.parent_id is None:
            raise HTTPException(409, "A ledger cannot sit directly under a main group")
        if led.system_key:
            raise HTTPException(409, "A system ledger cannot be moved")
        led.group_id = target.id
        record_audit(db, "ACC_LEDGER_MOVED", "ACCOUNTS", user, org_id, "AccLedger", str(led.id), {"to": target.name})
    else:
        g = _group(db, org_id, body.id)
        if g.is_system:
            raise HTTPException(409, "A standard group cannot be moved")
        if target.nature != g.nature:
            raise HTTPException(409, "A group can only be moved within its own main group")
        p = target
        while p is not None:
            if p.id == g.id:
                raise HTTPException(409, "A group cannot be moved under itself")
            p = db.get(AccGroup, p.parent_id) if p.parent_id else None
        g.parent_id = target.id
        record_audit(db, "ACC_GROUP_MOVED", "ACCOUNTS", user, org_id, "AccGroup", str(g.id), {"to": target.name})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "The target already holds an item with the same name")
    return {"ok": True}


@router.post("/sync")
def sync(user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    """Create any ledger missing for the parties, charges, banks and employees that already exist. Safe to repeat."""
    org_id = _tenant(user)
    n = coa.sync_all(db, org_id, user.id)
    db.commit()
    return n


@router.get("/export")
def export(user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    roots, _, _ = _build(db, org_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Type", "Code", "Name", "Group path", "Nature", "Opening balance", "Dr/Cr", "Status"])

    def walk(n):
        w.writerow(["Group", n["code"], n["name"], n["path"], n["nature"], "", "", n["status"]])
        for l in n["ledgers"]:
            w.writerow(["Ledger", l["code"], l["name"], n["path"], n["nature"], l["opening_balance"], l["opening_side"], l["status"]])
        for k in n["groups"]:
            walk(k)
    for r in roots:
        walk(r)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="chart-of-accounts.csv"'})
