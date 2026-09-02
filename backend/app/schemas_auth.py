from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    license_number: str = Field(min_length=1, max_length=20)


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


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: SafeUser
