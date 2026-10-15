"""Contracts for future provider adapters. No simulated financial success."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from datetime import datetime

@dataclass(frozen=True)
class Money:
    minor_units: int
    currency: str

@dataclass(frozen=True)
class ProviderReference:
    provider: str
    reference: str
    status: str  # accepted/processing are not settled

@dataclass(frozen=True)
class Quote:
    reference: str
    base: Money
    counter: Money
    rate: Decimal
    expires_at: datetime
    fees: Money

class EscrowProvider(Protocol):
    def open_escrow(self, transaction_id: str, contract_reference: str, amount: Money, idempotency_key: str) -> ProviderReference: ...
    def release(self, escrow_reference: str, beneficiary_reference: str, amount: Money, approval_id: str, idempotency_key: str) -> ProviderReference: ...

class FXProvider(Protocol):
    def quote(self, sell: Money, buy_currency: str) -> Quote: ...
    def book_quote(self, quote_reference: str, approval_id: str, idempotency_key: str) -> ProviderReference: ...

class BankingProvider(Protocol):
    def create_payment(self, bank_connection_reference: str, beneficiary_reference: str, amount: Money, approval_id: str, idempotency_key: str) -> ProviderReference: ...
    def payment_status(self, provider_reference: str) -> ProviderReference: ...
    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> tuple[str, dict]: ...

class ComplianceProvider(Protocol):
    def screen(self, case_id: str, evidence_references: list[str], idempotency_key: str) -> ProviderReference: ...

class UnconfiguredProvider:
    def __getattr__(self, operation):
        raise RuntimeError(f'Provider adapter is not configured for {operation}; execution blocked')
