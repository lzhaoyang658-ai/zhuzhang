from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


MAX_AMOUNT_CENTS = 10_000_000_000


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=60)
    area_sqm: int = Field(gt=0, le=10000)
    area_basis: str = "套内面积"
    renovation_type: str = "全包"
    address: str | None = Field(default=None, max_length=240)
    notes: str = Field(default="", max_length=2000)
    planned_start: date | None = None
    planned_end: date | None = None
    fund_limit_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    reserve_cents: int = Field(default=0, ge=0, le=MAX_AMOUNT_CENTS)
    status: str = Field(default="准备中", pattern="^(准备中|施工中|待结算)$")

    @model_validator(mode="after")
    def validate_project_boundaries(self):
        if self.planned_start and self.planned_end and self.planned_end < self.planned_start:
            raise ValueError("计划完工日期不能早于开工日期")
        if self.reserve_cents > self.fund_limit_cents:
            raise ValueError("风险预留金不能高于资金上限")
        return self


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    city: str | None = Field(default=None, min_length=1, max_length=60)
    area_sqm: int | None = Field(default=None, gt=0, le=10000)
    area_basis: str | None = Field(default=None, pattern="^(建筑面积|套内面积)$")
    renovation_type: str | None = Field(default=None, pattern="^(清包|半包|全包|整装)$")
    address: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=2000)
    planned_start: date | None = None
    planned_end: date | None = None
    fund_limit_cents: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_CENTS)
    reserve_cents: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_CENTS)
    fund_limit_reason: str | None = Field(default=None, min_length=2, max_length=240)


class BudgetCategoryUpdate(BaseModel):
    planned_limit_cents: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_CENTS)


class ProjectDeletionRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=120)


class ProjectExportCreate(BaseModel):
    include_attachments: bool = True
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def validate_export_dates(self):
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("导出结束日期不能早于开始日期")
        return self


class BaselineCreate(BaseModel):
    amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    reason: str = Field(default="确认合同报价", min_length=2, max_length=240)


class ChangeCreate(BaseModel):
    change_type: str = Field(pattern="^(increase|decrease)$")
    title: str = Field(min_length=2, max_length=160)
    reason: str = Field(min_length=2, max_length=400)
    content: str = Field(min_length=2, max_length=3000)
    amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    area: str | None = Field(default=None, max_length=80)
    category: str = Field(default="其他", min_length=1, max_length=80)
    proposer: str = Field(default="业主", max_length=80)
    proposed_on: date
    schedule_impact_days: int = Field(default=0, ge=-365, le=365)
    no_attachment_acknowledged: Literal[True]


class ChangeAction(BaseModel):
    comment: str = Field(default="", max_length=1000)


class MilestoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    planned_amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    planned_date: date
    condition: str = Field(min_length=2, max_length=300)
    required_acceptance: str = Field(default="阶段验收记录", max_length=200)


class AcceptanceCreate(BaseModel):
    accepted_on: date
    result: str = Field(pattern="^(passed|passed_with_issues|failed)$")
    notes: str = Field(default="", max_length=2000)
    open_issues: int = Field(default=0, ge=0, le=999)

    @model_validator(mode="after")
    def validate_issues(self):
        if self.result == "passed_with_issues" and self.open_issues < 1:
            raise ValueError("带问题通过时至少需要一个未关闭问题")
        return self


class PaymentCreate(BaseModel):
    amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    paid_on: date
    payee: str = Field(min_length=1, max_length=120)
    method: str = Field(default="银行转账", max_length=40)
    reference: str = Field(default="", max_length=160)
    override_reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=80)


class EvidenceMeta(BaseModel):
    evidence_type: str = Field(default="其他", max_length=60)
    description: str = Field(default="", max_length=400)
    related_type: str | None = Field(default=None, max_length=40)
    related_id: str | None = None


class QuoteItemUpdate(BaseModel):
    standard_name: str | None = Field(default=None, min_length=1, max_length=240)
    area: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    quantity: str | None = Field(default=None, max_length=40)
    unit: str | None = Field(default=None, max_length=30)
    unit_price_cents: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_CENTS)
    total_cents: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_CENTS)
    material_info: str | None = Field(default=None, max_length=1000)
    craft_notes: str | None = Field(default=None, max_length=2000)


class QuoteMatchGroupCreate(BaseModel):
    item_ids: list[str] = Field(min_length=2, max_length=3)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_unique_items(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("匹配条目不能重复")
        return self


class ProjectInviteCreate(BaseModel):
    email: str = Field(min_length=3, max_length=240, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: str = Field(pattern="^(co_manager|viewer)$")


class ProjectInviteAccept(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ProjectMembershipUpdate(BaseModel):
    role: str = Field(pattern="^(co_manager|viewer)$")


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=240, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class EmailCodeVerify(BaseModel):
    email: str = Field(min_length=3, max_length=240, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    code: str = Field(pattern=r"^\d{6}$")
    name: str | None = Field(default=None, min_length=1, max_length=80)


class EmailReauth(BaseModel):
    email: str = Field(min_length=3, max_length=240, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    code: str = Field(pattern=r"^\d{6}$")


class NotificationPreferenceUpdate(BaseModel):
    email_enabled: bool
    email_digest_frequency: str = Field(pattern="^(off|daily|weekly)$")
