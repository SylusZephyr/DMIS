"""Business database (PostgreSQL in production, SQLite embedded fallback).

Holds the people/organisation side of the platform -- users, employees,
categories, ownership, permissions, suppliers, companies -- plus the
operational records the UI needs: datasets, jobs and markets.

Analytical data (listings, products, metrics) does NOT live here; it lives
in the lake + DuckDB. The same SQLAlchemy models run on both engines.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from dip.settings import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(p.strip().lower() for p in parts).encode("utf-8")).hexdigest()[:16]


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="analyst")  # admin | manager | product_manager | analyst | viewer | customer
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # tenant; NULL = default organization
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiToken(Base):
    """Bearer tokens. Only the SHA-256 of the token is stored; the token itself is shown once."""
    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255), default="default")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    role: Mapped[str] = mapped_column(String(32))
    resource: Mapped[str] = mapped_column(String(64))     # e.g. "markets", "suppliers", "datasets"
    action: Mapped[str] = mapped_column(String(16))       # read | write | admin
    __table_args__ = (UniqueConstraint("role", "resource", "action"),)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)   # where summaries / alerts are sent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ownership: Mapped[list["Ownership"]] = relationship(back_populates="employee", cascade="all, delete-orphan")


class Category(Base):
    """A human category label (e.g. 微电机 / micromotor), optionally linked to a platform market."""
    __tablename__ = "categories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), unique=True)
    label_en: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_listing_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Ownership(Base):
    __tablename__ = "ownership"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    basis: Mapped[str] = mapped_column(String(255), default="ownership file")
    employee: Mapped[Employee] = relationship(back_populates="ownership")
    category: Mapped[Category] = relationship()
    __table_args__ = (UniqueConstraint("employee_id", "category_id"),)


class Company(Base):
    """A brand / manufacturer / seller seen in market data or supplier sources."""
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), default="brand")  # brand | seller | manufacturer
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    oem: Mapped[bool] = mapped_column(Boolean, default=False)
    odm: Mapped[bool] = mapped_column(Boolean, default=False)
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_categories: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price_level: Mapped[str | None] = mapped_column(String(16), nullable=True)   # low | mid | high (from quotes)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Dataset(Base):
    """One uploaded source file / API pull, stored immutably in the lake's raw zone."""
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    market_name: Mapped[str] = mapped_column(String(255))
    source_name: Mapped[str] = mapped_column(String(512))
    source_kind: Mapped[str] = mapped_column(String(32))     # sellersprite | auto_detect | api_json ...
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    raw_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # why a duplicate was allowed (override)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))              # process_dataset | import_v1 | rebuild_graph
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stages: Mapped[list | None] = mapped_column(JSON, default=list)  # [{name, status, seconds, summary}]
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Market(Base):
    """A processed market (one category's current state) with its headline summary."""
    __tablename__ = "markets"
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    industry_branch: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Consumables | Equipment | ...
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Event(Base):
    """Something that happened: a dataset processed, a competitor appeared, a price moved,
    a news item or supplier arrived from a connector. Immutable log."""
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64))            # e.g. competitor.new_brand, opportunity.change
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")   # info | notice | important
    source: Mapped[str] = mapped_column(String(64), default="pipeline")  # pipeline | connector:<name> | api
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Alert(Base):
    """An event routed to the employee responsible for the market."""
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(16), default="new")      # new | read | done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    event: Mapped[Event] = relationship()
    __table_args__ = (UniqueConstraint("event_id", "employee_id"),)


class AITrace(Base):
    """Every AI call the platform makes (PRINCIPLES.md principle 6): model, prompt version, input
    identifier, output, status, timestamp. Offline answers are not traced (no AI decision)."""
    __tablename__ = "ai_traces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    purpose: Mapped[str] = mapped_column(String(64))              # e.g. analyst.answer
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_ref: Mapped[str] = mapped_column(String(512))           # the question / record id
    input_hash: Mapped[str] = mapped_column(String(64))           # sha256 of the full prompt (facts included)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16))               # ok | unavailable | error | malformed | rejected
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # AI cost tracking (spec 166)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


DEFAULT_ORG = "default"


class Organization(Base):
    """A customer company (tenant). Markets, suppliers, datasets and users belong to one; rows with
    org_id NULL belong to the default organization, so single-company installs need nothing."""
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    plan: Mapped[str] = mapped_column(String(32), default="enterprise")   # config/platform/plans.yaml
    status: Mapped[str] = mapped_column(String(16), default="active")     # active | suspended
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UsageRecord(Base):
    """Metered usage per organization (records ingested, datasets, analyst questions, AI calls...)."""
    __tablename__ = "usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(32), default=DEFAULT_ORG)
    metric: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AcquisitionRun(Base):
    """One live-data acquisition (src/dip/acquire): what was fetched, from which providers, at what cost."""
    __tablename__ = "acquisition_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))               # market_snapshot | history_backfill | reviews
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running | done | partial | failed
    providers: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # capability -> provider used
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # terms, limits, flags
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # counts, snapshot files, job ids
    ledger: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # requests, cache hits, cost, errors
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WatchItem(Base):
    """A competitor listing someone chose to follow (src/dip/watch.py)."""
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("asin", "market_name", name="uq_watch_asin_market"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    asin: Mapped[str] = mapped_column(String(32), index=True)
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WatchObservation(Base):
    """One live observation of a watched listing (snapshot observations stay in the lake's observation_history)."""
    __tablename__ = "watch_observations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    asin: Mapped[str] = mapped_column(String(32), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales: Mapped[float | None] = mapped_column(Float, nullable=True)       # "bought in past month" badge floor
    bsr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OwnSale(Base):
    """Your own sales of one listing in one report period (Seller Central Business Report; src/dip/own_sales.py)."""
    __tablename__ = "own_sales"
    __table_args__ = (UniqueConstraint("asin", "period_start", "period_end", name="uq_own_sale_period"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    asin: Mapped[str] = mapped_column(String(32), index=True)
    parent_asin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_start: Mapped[str] = mapped_column(String(10))      # ISO date, inclusive
    period_end: Mapped[str] = mapped_column(String(10))        # ISO date, inclusive
    units: Mapped[float] = mapped_column(Float)
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    orders: Mapped[float | None] = mapped_column(Float, nullable=True)
    sessions: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_views: Mapped[float | None] = mapped_column(Float, nullable=True)
    conversion: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_box: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    imported_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourcingRun(Base):
    """One supplier search across marketplaces for a product concept (src/dip/sourcing_intel)."""
    __tablename__ = "sourcing_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    concept: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # what was searched for (spec, price, qty)
    queries: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # platform -> queries sent (EN / ZH)
    status: Mapped[str] = mapped_column(String(16), default="running")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # counts, best offer, shortlist
    ledger: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupplierInteraction(Base):
    """Cooperation history with a supplier: inquiries, quotes, samples, orders, audits, issues."""
    __tablename__ = "supplier_interactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    supplier_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))          # inquiry | quote | sample | order | audit | issue
    product: Mapped[str | None] = mapped_column(String(512), nullable=True)
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    moq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)      # 1..5 (quality / reliability of this interaction)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QueuedJob(Base):
    """Durable job queue (DIP_JOB_MODE=queue): the API enqueues, `dmis.py worker` processes."""
    __tablename__ = "job_queue"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(String(32))          # the tracked Job row
    kind: Mapped[str] = mapped_column(String(32), default="process_dataset")
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")   # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    """Every state-changing request and every important action: who, what, on which resource, result."""
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(128))              # e.g. "POST /api/v2/projects" or "project.approve"
    resource: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Project(Base):
    """An opportunity turned into a product-development project:
    opportunity -> evaluation -> supplier_search -> prototype -> launch_decision -> launched (tracked)."""
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(512))
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), default="opportunity")
    status: Mapped[str] = mapped_column(String(32), default="active")   # active | pending_approval | approved | rejected | closed
    owner_employee_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idea: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # title, specs, target price, unit cost
    prediction: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # launch evaluation at decision time
    tracked_listings: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ASINs once launched
    supplier_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProjectEvent(Base):
    """Decision history of a project: stage changes, recommendations, approvals, rejections, notes."""
    __tablename__ = "project_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(32))     # created | stage | recommend | approve | reject | note | outcome
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Comment(Base):
    """Comments, notes and product evaluations on any entity (product, segment, market, supplier, project)."""
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    target_kind: Mapped[str] = mapped_column(String(32))      # product | segment | market | supplier | project
    target_id: Mapped[str] = mapped_column(String(255))
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="comment")   # comment | note | evaluation
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # evaluations: 1..5
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Message(Base):
    """Internal messages: daily summaries, alert notifications, approval requests."""
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    to_employee_id: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="message")   # summary | alert | approval | message
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    emailed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ScopeDecision(Base):
    """A person's decision whether a marketplace sub-category belongs to a market (overrides the classifier)."""
    __tablename__ = "scope_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    market_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(512))
    decision: Mapped[str] = mapped_column(String(16))          # in | out | auto (= let the classifier decide)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IdentityDecision(Base):
    """A person's decision on whether two listings are the same product (spec 95-96). Applied on the next
    processing of the market, and used as evaluation data for the resolver (precision / recall, spec 132)."""
    __tablename__ = "identity_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    market_name: Mapped[str] = mapped_column(String(255), index=True)
    listing_a: Mapped[str] = mapped_column(String(64))
    listing_b: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))          # merge | keep_separate | needs_evidence
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaxonomyDecision(Base):
    """A person's decision on a machine-proposed taxonomy node (spec 9-10: machine-generated vs human-approved)."""
    __tablename__ = "taxonomy_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    market_name: Mapped[str] = mapped_column(String(255), index=True)
    node_key: Mapped[str] = mapped_column(String(512))         # stable key of the proposed node (dimension=value path)
    decision: Mapped[str] = mapped_column(String(16))          # approved | rejected | renamed
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LabelSample(Base):
    """Pilot (Master Prompt 4): a reproducible, stratified sample drawn for human accuracy labelling."""
    __tablename__ = "label_samples"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    market_name: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    seed: Mapped[int] = mapped_column(Integer)
    design: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # strata, allocation, population sizes
    dataset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LabelItem(Base):
    """One unit to label: a product (kind=product) or an excluded listing (kind=excluded_listing)."""
    __tablename__ = "label_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sample_id: Mapped[str] = mapped_column(ForeignKey("label_samples.id"))
    kind: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[str] = mapped_column(String(255))                         # product_id or listing record_id
    stratum: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # what the system said when drawn


class Label(Base):
    """A human judgement on one check of one item. The latest label per (item, check) counts."""
    __tablename__ = "labels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("label_items.id"))
    sample_id: Mapped[str] = mapped_column(String(32))
    check: Mapped[str] = mapped_column(String(32))     # relevance | entity | segment | best_listing | attributes
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    labeller: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserFeedback(Base):
    """"This looks wrong" reports from pilot users, triaged weekly."""
    __tablename__ = "user_feedback"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    target_kind: Mapped[str] = mapped_column(String(32))       # product | segment | market | alert | page
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    market_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    shown_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new")   # new | triaged | fixed | closed
    triage: Mapped[str | None] = mapped_column(String(32), nullable=True)  # data_bug | rule_fix | ui_confusion | feature_request | not_a_bug
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PageView(Base):
    """Pilot usage: which pages people open. Path only -- no page content."""
    __tablename__ = "page_views"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    path: Mapped[str] = mapped_column(String(512))
    user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


DEFAULT_PERMISSIONS = [
    ("admin", "*", "admin"),
    ("manager", "markets", "write"), ("manager", "suppliers", "write"), ("manager", "datasets", "write"),
    ("manager", "people", "write"),
    ("analyst", "markets", "read"), ("analyst", "suppliers", "read"), ("analyst", "datasets", "write"),
    ("analyst", "people", "read"),
    ("viewer", "markets", "read"), ("viewer", "suppliers", "read"), ("viewer", "people", "read"),
    # enterprise roles: product managers see the markets of their assigned categories (scoped in dip.auth);
    # customers see shopping mode only
    ("product_manager", "markets", "read"), ("product_manager", "datasets", "write"),
    ("product_manager", "suppliers", "read"), ("product_manager", "people", "read"), ("product_manager", "shopping", "read"),
    ("customer", "shopping", "read"),
    ("manager", "shopping", "read"), ("analyst", "shopping", "read"), ("viewer", "shopping", "read"),
    # operations (P3): projects (pipeline + approvals), supplier manager, audit
    ("manager", "projects", "admin"), ("product_manager", "projects", "write"), ("analyst", "projects", "write"),
    ("viewer", "projects", "read"),
    ("supplier_manager", "suppliers", "write"), ("supplier_manager", "markets", "read"),
    ("supplier_manager", "projects", "read"), ("supplier_manager", "people", "read"),
    ("supplier_manager", "shopping", "read"),
    # pilot (P4): accuracy labelling and feedback triage
    ("manager", "labels", "write"), ("analyst", "labels", "write"), ("product_manager", "labels", "write"),
    ("viewer", "labels", "read"), ("manager", "feedback", "write"),
    # competitor watchlist (src/dip/watch.py)
    ("manager", "watchlist", "admin"), ("product_manager", "watchlist", "write"), ("analyst", "watchlist", "write"),
    ("viewer", "watchlist", "read"), ("supplier_manager", "watchlist", "read"),
]


@lru_cache(maxsize=4)
def _engine(url: str):
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    Base.metadata.create_all(eng)
    migrate(eng)
    with Session(eng) as s:  # seed any default permission that is missing (existing DBs get new resources too)
        have = {(p.role, p.resource, p.action) for p in s.scalars(select(Permission))}
        s.add_all(Permission(role=r, resource=res, action=a) for r, res, a in DEFAULT_PERMISSIONS if (r, res, a) not in have)
        s.commit()
    return eng


def migrate(eng) -> list[str]:
    """Additive schema migration: add columns that the models define but an existing table lacks
    (new nullable columns only -- nothing is dropped or rewritten). Returns the columns added."""
    from sqlalchemy import inspect, text

    added = []
    insp = inspect(eng)
    with eng.begin() as con:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                con.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))
                added.append(f"{table.name}.{col.name}")
    return added


_ENGINE_LOCK = threading.Lock()


def engine():
    # lru_cache does not stop two threads from building the same engine at once; concurrent first requests
    # would then both run create_all and one fails with "table ... already exists"
    with _ENGINE_LOCK:
        return _engine(get_settings().business_url)


@contextmanager
def session() -> Session:
    s = sessionmaker(bind=engine(), expire_on_commit=False)()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def row_dict(obj) -> dict:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}


# ------------------------------------------------------------------ content-hash registry
class DuplicateContent(Exception):
    """The same file content is already registered under another market or snapshot period."""

    def __init__(self, message: str, conflicts: list[dict]):
        super().__init__(message)
        self.conflicts = conflicts


def duplicate_conflicts(content_hash: str, market: str, snapshot_date: str | None, org_id: str | None = None) -> list[dict]:
    """Earlier datasets of this organization with identical content but a different market or
    snapshot period. Re-uploading the same content for the same market and period is not a conflict."""
    org = org_id or DEFAULT_ORG
    with session() as s:
        q = s.query(Dataset).filter(Dataset.content_hash == content_hash)
        rows = [d for d in q.order_by(Dataset.created_at.asc()).all()
                if (d.org_id or DEFAULT_ORG) == org and (d.market_name != market or (d.snapshot_date or None) != (snapshot_date or None))]
        return [{"dataset_id": d.id, "market": d.market_name, "snapshot_date": d.snapshot_date,
                 "source_name": d.source_name} for d in rows]


def check_duplicate(content_hash: str, market: str, snapshot_date: str | None, org_id: str | None = None) -> None:
    """Raise ``DuplicateContent`` naming the existing market/period(s) when the content is already registered
    elsewhere: the same export uploaded as another period (or market) would fabricate a flat history."""
    found = duplicate_conflicts(content_hash, market, snapshot_date, org_id)
    if not found:
        return
    where = "; ".join(f"market '{c['market']}', snapshot {c['snapshot_date'] or '(none)'} (dataset {c['dataset_id']})"
                      for c in found[:5])
    raise DuplicateContent(
        f"identical content was already uploaded as {where}. Uploading it again as market '{market}', "
        f"snapshot {snapshot_date or '(none)'} would duplicate that data as a new period/market. "
        f"If this is intended, re-submit with allow_duplicate=true and a reason.", found)
