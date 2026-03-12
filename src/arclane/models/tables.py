"""Database models."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Business(Base):
    """A user's business — the core tenant unit."""

    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    owner_email: Mapped[str] = mapped_column(String(255), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(512))

    # Zuultimate/Vinzy references
    zuultimate_tenant_id: Mapped[str | None] = mapped_column(String(255))
    vinzy_license_key: Mapped[str | None] = mapped_column(String(255))

    # Plan & credits
    plan: Mapped[str] = mapped_column(String(50), default="starter")
    credits_remaining: Mapped[int] = mapped_column(Integer, default=5)
    credits_bonus: Mapped[int] = mapped_column(Integer, default=10)

    # Provisioning state
    subdomain_provisioned: Mapped[bool] = mapped_column(Boolean, default=False)
    email_provisioned: Mapped[bool] = mapped_column(Boolean, default=False)
    app_deployed: Mapped[bool] = mapped_column(Boolean, default=False)
    template: Mapped[str | None] = mapped_column(String(100))

    # Container management
    container_id: Mapped[str | None] = mapped_column(String(64))
    container_port: Mapped[int | None] = mapped_column(Integer)

    # Configuration the agents use
    agent_config: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    cycles: Mapped[list["Cycle"]] = relationship(back_populates="business")
    activity: Mapped[list["Activity"]] = relationship(back_populates="business")
    content: Mapped[list["Content"]] = relationship(back_populates="business")


class Cycle(Base):
    """An autonomous execution cycle (nightly or on-demand)."""

    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(50))  # "nightly" | "on_demand"
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending|running|completed|failed
    plan: Mapped[dict | None] = mapped_column(JSON)  # CoS-generated task plan
    result: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="cycles")


class Activity(Base):
    """Real-time activity log entry — feeds the SSE stream."""

    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"))
    agent: Mapped[str] = mapped_column(String(50))  # which executive acted (hidden label)
    action: Mapped[str] = mapped_column(String(100))  # user-friendly action name
    detail: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="activity")


class Content(Base):
    """Content produced by agents — blog posts, social posts, newsletters, etc."""

    __tablename__ = "content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    content_type: Mapped[str] = mapped_column(String(50))  # blog|social|newsletter|changelog|podcast
    title: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(50))  # twitter|linkedin|bluesky|instagram
    status: Mapped[str] = mapped_column(String(50), default="draft")  # draft|published|scheduled
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="content")


class Metric(Base):
    """Business metrics tracked over time."""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))  # traffic|leads|revenue|social_followers
    value: Mapped[float] = mapped_column()
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
