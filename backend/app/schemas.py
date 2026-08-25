from pydantic import BaseModel, ConfigDict


class OrganizationCreate(BaseModel):
    code: str
    name: str
    category: str = "BUS"
    country: str = "India"


class OrganizationRead(OrganizationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
