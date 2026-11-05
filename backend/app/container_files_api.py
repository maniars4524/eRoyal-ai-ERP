"""Photos and documents kept against containers, surveys, damages, repair orders, movements, EIRs and leases (JPEG / PNG / WebP / GIF / PDF, up to 5 MB each)."""
import base64
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_current_user, has_permission
from .auth_api import record_audit
from .containers_api import _org
from .db import get_db
from .models import (Container, ContainerAttachment, ContainerDamage, ContainerEIR, ContainerLease, ContainerMovement, ContainerRepairOrder, ContainerSurvey, Tyre, User, Vehicle, VehicleWorkOrder)

router = APIRouter(prefix="/api/container-attachments", tags=["container attachments"])
ENTITIES = {"container": Container, "survey": ContainerSurvey, "damage": ContainerDamage, "repair": ContainerRepairOrder, "movement": ContainerMovement, "eir": ContainerEIR, "lease": ContainerLease, "vehicle": Vehicle, "tyre": Tyre, "vorder": VehicleWorkOrder}
FLEET = {"vehicle", "tyre", "vorder"}
MAX_BYTES, MAX_PER_ENTITY = 5 * 1024 * 1024, 40
SIGNATURES = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/gif": (b"GIF87a", b"GIF89a"), "image/webp": (b"RIFF",), "application/pdf": (b"%PDF-",)}


class AttachmentIn(BaseModel):
    entity_type: str = Field(pattern="^(container|survey|damage|repair|movement|eir|lease|vehicle|tyre|vorder)$")
    entity_id: int
    name: str = Field(min_length=1, max_length=200)
    content_type: str = Field(max_length=100)
    data_base64: str
    caption: str | None = Field(default=None, max_length=300)


def _need(user: User, entity_type: str, level: str) -> None:
    """Container files follow the container permissions, vehicle / tyre / work-order files follow the fleet permissions."""
    code = ("fleet." if entity_type in FLEET else "container.") + level
    if not has_permission(user, code):
        raise HTTPException(403, "Permission denied")


def _row(a: ContainerAttachment) -> dict:
    return {"id": a.id, "entity_type": a.entity_type, "entity_id": a.entity_id, "category": a.category, "name": a.name, "content_type": a.content_type, "size": a.size, "caption": a.caption, "uploaded_at": a.created_at}


@router.get("")
def list_attachments(entity_type: str = Query(pattern="^(container|survey|damage|repair|movement|eir|lease|vehicle|tyre|vorder)$"), entity_id: int = Query(), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, entity_type, "read")
    org = _org(user)
    rows = db.scalars(select(ContainerAttachment).where(ContainerAttachment.organization_id == org, ContainerAttachment.entity_type == entity_type, ContainerAttachment.entity_id == entity_id).order_by(ContainerAttachment.id)).all()
    return [_row(a) for a in rows]


@router.post("", status_code=201)
def upload(body: AttachmentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, body.entity_type, "create")
    org = _org(user)
    model = ENTITIES[body.entity_type]
    if not db.scalar(select(model.id).where(model.id == body.entity_id, model.organization_id == org)):
        raise HTTPException(404, "The record to attach to was not found")
    ctype = body.content_type.lower().split(";")[0].strip()
    if ctype == "image/jpg":
        ctype = "image/jpeg"
    if ctype not in SIGNATURES:
        raise HTTPException(422, "Only JPEG, PNG, WebP, GIF photos and PDF documents can be attached")
    try:
        raw = base64.b64decode(body.data_base64, validate=True)
    except Exception:
        raise HTTPException(422, "The file could not be read")
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(422, "A file must be between 1 byte and 5 MB")
    if not any(raw.startswith(sig) for sig in SIGNATURES[ctype]):
        raise HTTPException(422, "The file content does not match its type")
    if (db.scalar(select(func.count()).select_from(ContainerAttachment).where(ContainerAttachment.organization_id == org, ContainerAttachment.entity_type == body.entity_type, ContainerAttachment.entity_id == body.entity_id)) or 0) >= MAX_PER_ENTITY:
        raise HTTPException(409, f"At most {MAX_PER_ENTITY} files can be attached to one record")
    name = re.sub(r"[\\/:*?\"<>|]+", "_", body.name.strip())[:200]
    a = ContainerAttachment(organization_id=org, entity_type=body.entity_type, entity_id=body.entity_id, category="PHOTO" if ctype.startswith("image/") else "DOCUMENT", name=name, content_type=ctype,
                            size=len(raw), caption=body.caption, data=raw, uploaded_by=user.id)
    db.add(a)
    db.flush()
    record_audit(db, "CONTAINER_FILE_ADDED", "INVENTORY", user, org, body.entity_type, str(body.entity_id), {"file": name, "size": len(raw)})
    db.commit()
    return _row(a)


@router.get("/{attachment_id}/file")
def download(attachment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = _org(user)
    a = db.scalar(select(ContainerAttachment).where(ContainerAttachment.id == attachment_id, ContainerAttachment.organization_id == org))
    if not a:
        raise HTTPException(404, "File not found")
    _need(user, a.entity_type, "read")
    return Response(content=a.data, media_type=a.content_type, headers={"Content-Disposition": 'inline; filename="' + a.name.replace('"', "") + '"', "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.delete("/{attachment_id}", status_code=204)
def remove(attachment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Only approvers remove a file, and the removal is written to the audit log."""
    org = _org(user)
    a = db.scalar(select(ContainerAttachment).where(ContainerAttachment.id == attachment_id, ContainerAttachment.organization_id == org))
    if not a:
        raise HTTPException(404, "File not found")
    _need(user, a.entity_type, "approve")
    record_audit(db, "CONTAINER_FILE_REMOVED", "INVENTORY", user, org, a.entity_type, str(a.entity_id), {"file": a.name})
    db.delete(a)
    db.commit()
