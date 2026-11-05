"""Cargo calculation service for screens: the browser sends the dimension rows, the server returns the authoritative figures (the same engine the job, quotation and costing use)."""
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import cargo_engine as ce
from .auth import get_current_user, has_permission
from .models import User

router = APIRouter(prefix="/api/cargo", tags=["cargo calculation"])


class RowIn(BaseModel):
    packages: Decimal | str | None = None
    package_type: str | None = Field(default=None, max_length=40)
    length: Decimal | str | None = None
    width: Decimal | str | None = None
    height: Decimal | str | None = None
    unit: str = Field(default="CM", max_length=8)
    gross: Decimal | str | None = None
    gross_unit: str = Field(default="KG", max_length=8)
    weight_basis: str = Field(default="TOTAL", max_length=12)
    remarks: str | None = Field(default=None, max_length=200)


class CalcIn(BaseModel):
    mode: str = Field(default="Air", max_length=20)
    shipment_type: str = Field(default="", max_length=20)
    rows: list[RowIn] = Field(default_factory=list, max_length=200)
    shipment_gross_kg: Decimal | str | None = None
    express: bool = False


def _allowed(user: User) -> None:
    if not any(has_permission(user, c) for c in ("shipment.read", "quote.read")):
        raise HTTPException(403, "Permission denied")


@router.get("/config")
def effective_config(express: bool = False, user: User = Depends(get_current_user)):
    _allowed(user)
    c = ce.default_config(express)
    return {"divisor": str(c.divisor), "rounding": c.rounding, "rt_rounding": c.rt_rounding, "wm_kg_per_cbm": str(c.wm_kg_per_cbm), "min_chargeable_kg": str(c.min_chargeable_kg), "min_rt": str(c.min_rt),
            "reference": c.reference, "length_units": list(ce.LENGTH_CM), "weight_units": list(ce.WEIGHT_KG), "rounding_rules": list(ce.ROUNDING)}


@router.post("/calc")
def calc(body: CalcIn, user: User = Depends(get_current_user)) -> dict[str, Any]:
    _allowed(user)
    try:
        return ce.compute([r.model_dump() for r in body.rows], body.mode, body.shipment_type, ce.default_config(body.express), body.shipment_gross_kg)
    except ce.CargoError as e:
        raise HTTPException(422, str(e))
