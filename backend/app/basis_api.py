"""The Basis calculation service over HTTP: the quotation and job cost screens ask it for the quantity / amount of every line, so the numbers come from one place."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import basis_calc as bc
from .auth import get_current_user
from .models import User

router = APIRouter(prefix="/api/basis", tags=["basis"])


class MeasuresIn(BaseModel):
    mode: str = "Sea"
    shipment_type: str = ""
    packages: Any = None
    gross_kg: Any = None
    net_kg: Any = None
    volume_cbm: Any = None
    chargeable_kg: Any = None
    vehicles: Any = None
    dims: list[dict] = Field(default_factory=list, max_length=100)
    divisor: Any = None


class LineIn(BaseModel):
    id: Any = None
    side: str = "SELL"
    basis: str = ""
    rate: Any = None
    qty: Any = None
    name: str = ""
    category: str = ""
    ccy: str = "INR"
    exch: Any = 1
    include: list[str] | None = None        # "% of Other Charges": the charges the percentage is taken from


class CalcIn(BaseModel):
    measures: MeasuresIn = Field(default_factory=MeasuresIn)
    lines: list[LineIn] = Field(default_factory=list, max_length=400)


@router.get("/bases")
def bases(user: User = Depends(get_current_user)):
    return {"bases": [{"basis": b, "unit": bc.UNITS[b], "percent": b in bc.PERCENT_BASES} for b in bc.BASES], "air_volumetric_divisor": int(bc.divisor_default())}


@router.post("/calc")
def calculate(body: CalcIn, user: User = Depends(get_current_user)):
    try:
        m = bc.Measures(**body.measures.model_dump())
    except bc.BasisError as e:
        raise HTTPException(422, str(e))
    lines = [{**ln.model_dump(), "id": ln.id if ln.id is not None else i} for i, ln in enumerate(body.lines)]
    return bc.calc_lines(lines, m)
