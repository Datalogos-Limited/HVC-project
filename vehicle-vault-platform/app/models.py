from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, SecretStr

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Credentials(Model):
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: SecretStr = Field(min_length=12, max_length=128)

class SignIn(Model):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

class Client(Model):
    name: str = Field(min_length=1, max_length=200)
    entity_type: Literal['individual', 'company', 'trust', 'family_office']
    domicile: str = Field(pattern=r'^[A-Z]{2}$')

class Money(Model):
    minor_units: int = Field(ge=0, le=9_000_000_000_000_000)
    currency: Literal['GBP', 'USD', 'EUR', 'CHF', 'JPY']

class PositiveMoney(Money):
    minor_units: int = Field(gt=0, le=9_000_000_000_000_000)

class Asset(Model):
    client_id: UUID
    make: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=1886, le=2100)
    chassis_number: str = Field(min_length=1, max_length=80)
    category: Literal['classic_vintage', 'supercar', 'hypercar', 'other']
    purchase: Money
    valuation: Money
    title_status: Literal['unverified', 'clear', 'financed', 'disputed'] = 'unverified'
    provenance: str = Field(default='', max_length=8000)
    details: dict[str, str] = Field(default_factory=dict, max_length=20)
    annual_costs: dict[str, Money] = Field(default_factory=dict, max_length=10)

class KycCase(Model):
    client_id: UUID
    subject_type: Literal['individual', 'company', 'trust']
    purpose: Literal['onboarding', 'periodic_review', 'transaction_review']

class TransactionDraft(Model):
    client_id: UUID
    asset_id: UUID
    amount: PositiveMoney
    direction: Literal['purchase', 'sale']

class DocumentCreate(Model):
    parent_id: UUID
    filename: str = Field(min_length=1, max_length=180, pattern=r'^[^/\\\x00-\x1f]+$')
    category: Literal['identity', 'address', 'source_of_funds', 'mandate', 'title', 'insurance', 'valuation', 'service', 'inspection', 'contract', 'bank_statement', 'other']
    content_type: Literal['application/pdf', 'image/jpeg', 'image/png']
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

class RecordEnvelope(Model):
    id: UUID
    client_id: UUID
    version: int
    created_at: str
    data: dict

class RecordPage(Model):
    items: list[RecordEnvelope]
    next_cursor: UUID | None

class TokenResponse(Model):
    id_token: str
    refresh_token: str
    expires_in: int
    user_id: str

class IdentityResponse(Model):
    user_id: str
    tenant_id: UUID
    roles: list[str]
    client_id: UUID | None

class UploadResponse(Model):
    document_id: UUID
    status: Literal['quarantined']
