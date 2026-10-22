import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    license_number: str | None = Field(default=None, max_length=20)
    portal: str | None = Field(default=None, pattern="^(developer|erp|demo)$")
    replace_session: bool = False


class OrganizationAdminCreate(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    country: str = Field(default="India", max_length=80)
    admin_email: str = Field(min_length=5, max_length=254)
    admin_username: str = Field(min_length=2, max_length=80)
    admin_password: str = Field(min_length=12, max_length=256)
    admin_first_name: str = Field(min_length=1, max_length=80)
    admin_last_name: str = Field(default="", max_length=80)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


PARTY_ROLES = {"shipper", "consignee", "carrier", "agent", "cha", "transporter", "ar", "ap", "customer", "vendor", "iata", "cfs", "origin_agent", "coloader"}
CLIENT_ROLES = ["shipper", "consignee", "ar", "customer"]
VENDOR_ROLES = ["ap", "carrier", "agent", "cha", "transporter", "vendor", "iata", "cfs", "origin_agent", "coloader"]
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
DATA_KEY_RE = re.compile(r"^[a-z0-9_]{1,60}$")


def _clean_roles(value):
    roles = []
    for role in value:
        role = str(role).strip().lower()
        if role not in PARTY_ROLES:
            raise ValueError(f"unknown role: {role[:20]}")
        if role not in roles:
            roles.append(role)
    if not roles:
        raise ValueError("choose at least one role")
    return roles


def _clean_party_data(value):
    if len(value) > 200:
        raise ValueError("too many fields")
    for key, item in value.items():
        if not DATA_KEY_RE.match(key):
            raise ValueError(f"invalid field name: {key[:30]}")
        if isinstance(item, list):
            if len(item) > 100 or not all(isinstance(r, dict) and len(r) <= 20 and all(isinstance(v, (str, int, float, bool, type(None))) for v in r.values()) for r in item):
                raise ValueError(f"field {key} must be a list of simple rows")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"field {key} must be a simple value or a list of rows")
        if isinstance(item, str) and len(item) > 2000:
            raise ValueError(f"field {key} is too long")
    return value


class CustomerCreate(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=160)
    short_name: str | None = Field(default=None, max_length=60)
    category: str = Field(default="Business", max_length=40)
    country: str = Field(default="India", max_length=80)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    credit_limit: float = Field(default=0, ge=0)
    payment_terms: str | None = Field(default=None, max_length=40)
    roles: list[str] = Field(default_factory=lambda: ["shipper"])
    data: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")

    @field_validator("code")
    @classmethod
    def _code(cls, value):
        value = value.strip().upper()
        if not re.match(r"^[A-Z0-9._/\- ]{2,20}$", value):
            raise ValueError("code may only contain letters, digits and . _ / - space")
        return value

    @field_validator("roles")
    @classmethod
    def _roles(cls, value):
        return _clean_roles(value)

    @field_validator("data")
    @classmethod
    def _data(cls, value):
        return _clean_party_data(value)

    @model_validator(mode="after")
    def _tax_ids(self):
        if self.gstin:
            self.gstin = self.gstin.strip().upper()
        if self.pan:
            self.pan = self.pan.strip().upper()
        if (self.country or "").strip().lower() == "india":
            if self.gstin and not GSTIN_RE.match(self.gstin):
                raise ValueError("GSTIN format is invalid (expected 15 characters, e.g. 27AAACT2727Q1ZN)")
            if self.pan and not PAN_RE.match(self.pan):
                raise ValueError("PAN format is invalid (expected 10 characters, e.g. AAACT2727Q)")
        return self


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    short_name: str | None = Field(default=None, max_length=60)
    category: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=80)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    credit_limit: float | None = Field(default=None, ge=0)
    payment_terms: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    roles: list[str] | None = None
    data: dict[str, Any] | None = None

    @field_validator("roles")
    @classmethod
    def _roles(cls, value):
        return None if value is None else _clean_roles(value)

    @field_validator("data")
    @classmethod
    def _data(cls, value):
        return None if value is None else _clean_party_data(value)

    @model_validator(mode="after")
    def _tax_ids(self):
        if self.gstin:
            self.gstin = self.gstin.strip().upper()
        if self.pan:
            self.pan = self.pan.strip().upper()
        if (self.country is None or self.country.strip().lower() == "india"):
            if self.gstin and not GSTIN_RE.match(self.gstin):
                raise ValueError("GSTIN format is invalid (expected 15 characters, e.g. 27AAACT2727Q1ZN)")
            if self.pan and not PAN_RE.match(self.pan):
                raise ValueError("PAN format is invalid (expected 10 characters, e.g. AAACT2727Q)")
        return self


class PortCreate(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    unlocode: str | None = Field(default=None, max_length=10)
    port_type: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    country: str = Field(min_length=2, max_length=80)
    timezone: str | None = Field(default=None, max_length=60)


class PortUpdate(BaseModel):
    unlocode: str | None = Field(default=None, max_length=10)
    port_type: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=60)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


class ShipmentCreate(BaseModel):
    job_type: str = Field(default="Forwarding", max_length=20)
    mode: str = Field(min_length=2, max_length=20)
    direction: str = Field(min_length=2, max_length=20)
    container_mode: str | None = Field(default=None, max_length=20)
    department: str | None = Field(default=None, max_length=20)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    etd: date | None = None
    eta: date | None = None
    customer_id: int | None = None
    party_branch_id: int | None = None
    consignee: str | None = Field(default=None, max_length=160)
    packages: int | None = Field(default=None, ge=0)
    package_type: str | None = Field(default=None, max_length=40)
    gross_weight: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    commodity: str | None = Field(default=None, max_length=160)
    bl_awb: str | None = Field(default=None, max_length=60)
    revenue: float = Field(default=0, ge=0)


class ShipmentUpdate(BaseModel):
    job_type: str | None = Field(default=None, max_length=20)
    mode: str | None = Field(default=None, max_length=20)
    direction: str | None = Field(default=None, max_length=20)
    container_mode: str | None = Field(default=None, max_length=20)
    department: str | None = Field(default=None, max_length=20)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    etd: date | None = None
    eta: date | None = None
    customer_id: int | None = None
    consignee: str | None = Field(default=None, max_length=160)
    packages: int | None = Field(default=None, ge=0)
    package_type: str | None = Field(default=None, max_length=40)
    gross_weight: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    commodity: str | None = Field(default=None, max_length=160)
    bl_awb: str | None = Field(default=None, max_length=60)
    revenue: float | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=30)


class SafeUser(BaseModel):
    id: int
    email: str
    username: str | None
    first_name: str
    last_name: str
    organization_id: int | None
    organization_name: str
    is_super_admin: bool
    roles: list[str]
    permissions: list[str]
    portal: str
    theme: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: SafeUser
