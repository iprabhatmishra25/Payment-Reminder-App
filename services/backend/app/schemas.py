import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Login(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MFA(Strict):
    challenge: str
    code: str = Field(pattern=r'^\d{6}$')


class CompanyInput(Strict):
    name: str = Field(min_length=2, max_length=160)
    code: str = Field(min_length=2, max_length=30, pattern=r'^[a-zA-Z0-9_-]+$')
    email: EmailStr
    contact_name: str = Field(default='', max_length=120)
    industry: str = Field(default='Other', max_length=80)


class CompanyUpdate(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    email: EmailStr | None = None
    contact_name: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=80)
    active: bool | None = None
    paused: bool | None = None


class InvoiceInput(Strict):
    company_id: str
    number: str = Field(min_length=2, max_length=50)
    billing_month: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    issue_date: date
    due_date: date
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    lifecycle: Literal['DRAFT', 'POSTED'] = 'POSTED'

    @model_validator(mode='after')
    def dates(self):
        if self.due_date < self.issue_date:
            raise ValueError('Due date must be on or after issue date')
        if not 2000 <= int(self.billing_month[:4]) <= 2200:
            raise ValueError('Billing year must be between 2000 and 2200')
        return self


class PaymentInput(Strict):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    paid_at: datetime
    method: Literal['BANK_TRANSFER', 'UPI', 'CHEQUE', 'CASH', 'OTHER'] = 'BANK_TRANSFER'
    reference: str = Field(default='', max_length=150)

    @field_validator('paid_at')
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None:
            raise ValueError('Include a timezone in paid_at')
        return v


class Reason(Strict):
    reason: str = Field(min_length=5, max_length=500)


class RunInput(Strict):
    company_ids: list[str] | None = Field(default=None, max_length=2000)


class PolicyInput(Strict):
    day: int = Field(ge=1, le=31)
    time: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone: str
    enabled: bool
    catchup_hours: int = Field(default=48, ge=1, le=168)
    subject: str = Field(min_length=3, max_length=160)
    message: str = Field(min_length=10, max_length=3000)

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError('Use a valid named timezone, such as Asia/Kolkata')
        return value


class UserInput(Strict):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    role: Literal['admin', 'accounts', 'viewer']


class Reset(Strict):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=128)
