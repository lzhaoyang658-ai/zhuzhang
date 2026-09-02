from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey,
                        Index, Integer, JSON, String, Text, UniqueConstraint,
                        text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "source_file_count >= 0",
            name="ck_projects_source_file_count_nonnegative",
        ),
        CheckConstraint(
            "source_bytes >= 0",
            name="ck_projects_source_bytes_nonnegative",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(60))
    area_sqm: Mapped[int] = mapped_column(Integer)
    area_basis: Mapped[str] = mapped_column(String(20), default="套内面积")
    renovation_type: Mapped[str] = mapped_column(String(20), default="全包")
    address: Mapped[str | None] = mapped_column(String(240), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    fund_limit_cents: Mapped[int] = mapped_column(Integer)
    reserve_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="施工中")
    status_before_deletion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_file_count: Mapped[int] = mapped_column(Integer, default=0)
    source_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    baselines: Mapped[list[BaselineVersion]] = relationship(back_populates="project")
    changes: Mapped[list[ChangeOrder]] = relationship(back_populates="project")
    milestones: Mapped[list[PaymentMilestone]] = relationship(back_populates="project")
    quotes: Mapped[list[Quote]] = relationship(back_populates="project")
    memberships: Mapped[list[ProjectMembership]] = relationship(back_populates="project", cascade="all, delete-orphan")
    invites: Mapped[list[ProjectInvite]] = relationship(back_populates="project", cascade="all, delete-orphan")
    budget_categories: Mapped[list[ProjectBudgetCategory]] = relationship(back_populates="project", cascade="all, delete-orphan")
    fund_limit_history: Mapped[list[ProjectFundLimitHistory]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectBudgetCategory(Base):
    __tablename__ = "project_budget_categories"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_project_budget_category_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    planned_limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="budget_categories")


class ProjectFundLimitHistory(Base):
    __tablename__ = "project_fund_limit_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    previous_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(240), default="创建项目")
    changed_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    changed_by_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="fund_limit_history")


class DeletedProjectRecord(Base):
    __tablename__ = "deleted_project_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_reference_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    business_record_count: Mapped[int] = mapped_column(Integer, default=0)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)


class ProjectExportJob(Base):
    __tablename__ = "project_export_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=5)
    stage: Mapped[str] = mapped_column(String(120), default="等待生成")
    include_attachments: Mapped[bool] = mapped_column(Boolean, default=True)
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_backend: Mapped[str] = mapped_column(String(30), default="local")
    report_version: Mapped[str] = mapped_column(String(30), default="formal-v2")
    report_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_count: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    artifacts: Mapped[list[ProjectExportArtifact]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ProjectExportArtifact.part_number"
    )


class ProjectExportArtifact(Base):
    __tablename__ = "project_export_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("project_export_jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="archive")
    part_number: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(180))
    object_key: Mapped[str] = mapped_column(String(240), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_backend: Mapped[str] = mapped_column(String(30), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[ProjectExportJob] = relationship(back_populates="artifacts")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    memberships: Mapped[list[ProjectMembership]] = relationship(back_populates="user", cascade="all, delete-orphan")
    login_sessions: Mapped[list[LoginSession]] = relationship(back_populates="user", cascade="all, delete-orphan")


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_notification_preference_user"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    email_digest_frequency: Mapped[str] = mapped_column(String(20), default="off")
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_notification_dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="risk")
    code: Mapped[str] = mapped_column(String(20), index=True)
    level: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action_path: Mapped[str] = mapped_column(String(240), default="/")
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LoginChallenge(Base):
    __tablename__ = "login_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(240), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    request_ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(240), default="未知设备")
    ip_hash: Mapped[str] = mapped_column(String(64))
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship(back_populates="login_sessions")


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_project_membership_user_project"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship(back_populates="memberships")
    project: Mapped[Project] = relationship(back_populates="memberships")


class ProjectInvite(Base):
    __tablename__ = "project_invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    email: Mapped[str] = mapped_column(String(240), index=True)
    role: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    invited_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    project: Mapped[Project] = relationship(back_populates="invites")


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        CheckConstraint(
            "source_size_bytes >= 0",
            name="ck_quotes_source_size_bytes_nonnegative",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR length(source_sha256) = 64",
            name="ck_quotes_source_sha256_length",
        ),
        CheckConstraint(
            "scan_status IN ('legacy_unscanned', 'pending', 'clean', 'skipped', 'infected', 'error')",
            name="ck_quotes_scan_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    original_name: Mapped[str] = mapped_column(String(240))
    object_key: Mapped[str] = mapped_column(String(120))
    source_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scan_status: Mapped[str] = mapped_column(String(24), default="legacy_unscanned")
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="reviewing")
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    source_total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_type: Mapped[str] = mapped_column(String(30), default="spreadsheet")
    parse_method: Mapped[str] = mapped_column(String(40), default="deterministic_table")
    parser_version: Mapped[str] = mapped_column(String(40), default="beta-1")
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="quotes")
    items: Mapped[list[QuoteItem]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    corrections: Mapped[list[QuoteCorrection]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    parse_jobs: Mapped[list[QuoteParseJob]] = relationship(back_populates="quote", cascade="all, delete-orphan")


class QuoteItem(Base):
    __tablename__ = "quote_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(240))
    standard_name: Mapped[str] = mapped_column(String(240))
    area: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str] = mapped_column(String(80), default="其他")
    quantity_text: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    unit_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cents: Mapped[int] = mapped_column(Integer)
    source_location: Mapped[str] = mapped_column(String(80))
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=100)
    field_confidences: Mapped[dict] = mapped_column(JSON, default=dict)
    material_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    craft_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote: Mapped[Quote] = relationship(back_populates="items")


class QuoteCorrection(Base):
    __tablename__ = "quote_corrections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    quote_item_id: Mapped[str] = mapped_column(ForeignKey("quote_items.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(60))
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(80), default="项目所有者")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    quote: Mapped[Quote] = relationship(back_populates="corrections")


class QuoteParseJob(Base):
    __tablename__ = "quote_parse_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=5)
    stage: Mapped[str] = mapped_column(String(120), default="等待解析")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    quote: Mapped[Quote] = relationship(back_populates="parse_jobs")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    queue_name: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="idle")
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class QuoteMatchGroup(Base):
    __tablename__ = "quote_match_groups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(240))
    created_by: Mapped[str] = mapped_column(String(80), default="项目所有者")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    members: Mapped[list[QuoteMatchMember]] = relationship(back_populates="group", cascade="all, delete-orphan")


class QuoteMatchMember(Base):
    __tablename__ = "quote_match_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    group_id: Mapped[str] = mapped_column(ForeignKey("quote_match_groups.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    quote_item_id: Mapped[str] = mapped_column(ForeignKey("quote_items.id"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    group: Mapped[QuoteMatchGroup] = relationship(back_populates="members")
    quote_item: Mapped[QuoteItem] = relationship()


class BaselineVersion(Base):
    __tablename__ = "baseline_versions"
    __table_args__ = (
        Index(
            "uq_baseline_versions_project_version",
            "project_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_baseline_versions_active_project",
            "project_id",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active = 1"),
        ),
        CheckConstraint("amount_cents > 0", name="ck_baseline_versions_amount_positive"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(240), default="首次确认合同报价")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed_by: Mapped[str] = mapped_column(String(80), default="项目所有者")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"), nullable=True, index=True)
    project: Mapped[Project] = relationship(back_populates="baselines")


class ChangeOrder(Base):
    __tablename__ = "change_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    change_type: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(String(400))
    content: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    area: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str] = mapped_column(String(80), default="其他")
    proposer: Mapped[str] = mapped_column(String(80), default="业主")
    proposed_on: Mapped[date] = mapped_column(Date, default=date.today)
    schedule_impact_days: Mapped[int] = mapped_column(Integer, default=0)
    no_attachment_acknowledged: Mapped[bool] = mapped_column(Boolean, default=True)
    share_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    share_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmation_role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmation_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    project: Mapped[Project] = relationship(back_populates="changes")


class PaymentMilestone(Base):
    __tablename__ = "payment_milestones"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    planned_amount_cents: Mapped[int] = mapped_column(Integer)
    planned_date: Mapped[date] = mapped_column(Date)
    condition: Mapped[str] = mapped_column(String(300))
    required_acceptance: Mapped[str] = mapped_column(String(200), default="阶段验收记录")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[Project] = relationship(back_populates="milestones")
    acceptances: Mapped[list[AcceptanceRecord]] = relationship(back_populates="milestone")
    payments: Mapped[list[PaymentRecord]] = relationship(back_populates="milestone")


class AcceptanceRecord(Base):
    __tablename__ = "acceptance_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("payment_milestones.id"), index=True)
    accepted_on: Mapped[date] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="")
    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    recorded_by: Mapped[str] = mapped_column(String(80), default="项目所有者")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    milestone: Mapped[PaymentMilestone] = relationship(back_populates="acceptances")


class PaymentRecord(Base):
    __tablename__ = "payment_records"
    __table_args__ = (
        Index(
            "uq_payment_records_project_idempotency_key",
            "project_id",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "uq_payment_records_reversal_of_payment_id",
            "reversal_of_payment_id",
            unique=True,
        ),
        CheckConstraint("amount_cents > 0", name="ck_payment_records_amount_positive"),
        CheckConstraint(
            "record_type IN ('normal', 'reversal')",
            name="ck_payment_records_record_type",
        ),
        CheckConstraint(
            "((record_type = 'normal' AND reversal_of_payment_id IS NULL) "
            "OR (record_type = 'reversal' AND reversal_of_payment_id IS NOT NULL))",
            name="ck_payment_records_reversal_shape",
        ),
        CheckConstraint(
            "((idempotency_key IS NULL AND request_fingerprint IS NULL) "
            "OR (idempotency_key IS NOT NULL AND length(request_fingerprint) = 64))",
            name="ck_payment_records_idempotency_fingerprint",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("payment_milestones.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_on: Mapped[date] = mapped_column(Date)
    payee: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(40), default="银行转账")
    reference: Mapped[str] = mapped_column(String(160), default="")
    record_type: Mapped[str] = mapped_column(String(20), default="normal")
    controlled: Mapped[bool] = mapped_column(Boolean, default=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reversal_of_payment_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    milestone: Mapped[PaymentMilestone] = relationship(back_populates="payments")


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_evidence_size_bytes_nonnegative",
        ),
        CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_evidence_sha256_length",
        ),
        CheckConstraint(
            "scan_status IN ('legacy_unscanned', 'pending', 'clean', 'skipped', 'infected', 'error')",
            name="ck_evidence_scan_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(240))
    object_key: Mapped[str] = mapped_column(String(120))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scan_status: Mapped[str] = mapped_column(String(24), default="legacy_unscanned")
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(400), default="")
    related_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    related_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    amount_delta_cents: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str] = mapped_column(String(80), default="项目所有者")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
