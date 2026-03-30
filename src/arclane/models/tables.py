"""Database models."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
    website_url: Mapped[str | None] = mapped_column(String(500))
    website_summary: Mapped[str | None] = mapped_column(Text)
    owner_email: Mapped[str] = mapped_column(String(255), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(512))

    # Zuultimate/Vinzy references
    zuultimate_tenant_id: Mapped[str | None] = mapped_column(String(255))
    vinzy_license_key: Mapped[str | None] = mapped_column(String(255))

    # Plan & working days
    plan: Mapped[str] = mapped_column(String(50), default="preview")
    working_days_remaining: Mapped[int] = mapped_column(Integer, default=3, name="credits_remaining")
    working_days_bonus: Mapped[int] = mapped_column(Integer, default=0, name="credits_bonus")

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

    # Stripe Connect
    stripe_connect_id: Mapped[str | None] = mapped_column(String(255))  # acct_xxx
    stripe_connect_onboarded: Mapped[bool] = mapped_column(Boolean, default=False)

    # Roadmap / incubator progress
    roadmap_day: Mapped[int] = mapped_column(Integer, default=0)
    current_phase: Mapped[int] = mapped_column(Integer, default=0)  # 0=not started, 1-4=phases
    graduation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_score: Mapped[float | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    cycles: Mapped[list["Cycle"]] = relationship(back_populates="business")
    activity: Mapped[list["Activity"]] = relationship(back_populates="business")
    content: Mapped[list["Content"]] = relationship(back_populates="business")
    roadmap_phases: Mapped[list["RoadmapPhase"]] = relationship(back_populates="business")
    milestones: Mapped[list["Milestone"]] = relationship(back_populates="business")
    health_scores: Mapped[list["BusinessHealthScore"]] = relationship(back_populates="business")
    revenue_events: Mapped[list["RevenueEvent"]] = relationship(back_populates="business")
    advisory_notes: Mapped[list["AdvisoryNote"]] = relationship(back_populates="business")
    distribution_channels: Mapped[list["DistributionChannel"]] = relationship(back_populates="business")
    competitive_monitors: Mapped[list["CompetitiveMonitor"]] = relationship(back_populates="business")
    support_tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="business")
    ad_campaigns: Mapped[list["AdCampaign"]] = relationship(back_populates="business")
    customer_segments: Mapped[list["CustomerSegment"]] = relationship(back_populates="business")


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
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), index=True)
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
    content_type: Mapped[str] = mapped_column(String(50), index=True)  # blog|social|newsletter|changelog|podcast
    title: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(50))  # twitter|linkedin|bluesky|instagram
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)  # draft|published|scheduled
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    milestone_key: Mapped[str | None] = mapped_column(String(100))
    distribution_status: Mapped[str | None] = mapped_column(String(50))  # pending|distributed|failed
    distribution_results: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="content")
    performance: Mapped[list["ContentPerformance"]] = relationship(back_populates="content")


class FailedWebhook(Base):
    """Webhook deliveries that failed processing and are queued for retry."""

    __tablename__ = "failed_webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(200))  # which handler failed
    payload: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Metric(Base):
    """Business metrics tracked over time."""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), index=True)  # traffic|leads|revenue|social_followers
    value: Mapped[float] = mapped_column()
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RoadmapPhase(Base):
    """Tracks phase progress for a business's 90-day incubator program."""

    __tablename__ = "roadmap_phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    phase_number: Mapped[int] = mapped_column(Integer)  # 1-4
    phase_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="locked")  # locked|active|completed
    graduation_score: Mapped[float | None] = mapped_column(default=None)
    graduation_criteria: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="roadmap_phases")


class Milestone(Base):
    """Trackable milestone within a roadmap phase."""

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    phase_number: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(50))  # deliverable|metric|gate
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending|in_progress|completed|skipped
    target_value: Mapped[float | None] = mapped_column(default=None)
    current_value: Mapped[float | None] = mapped_column(default=None)
    evidence_json: Mapped[dict | None] = mapped_column(JSON)
    due_day: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="milestones")


class BusinessHealthScore(Base):
    """Point-in-time health score snapshot."""

    __tablename__ = "business_health_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    score_type: Mapped[str] = mapped_column(String(50))  # overall|market_fit|content|revenue|operations
    score: Mapped[float] = mapped_column()  # 0-100
    factors: Mapped[dict | None] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="health_scores")


class RevenueEvent(Base):
    """Revenue event from external sources (Stripe, Gumroad, Shopify, etc.)."""

    __tablename__ = "revenue_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))  # stripe|gumroad|shopify|manual
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(10), default="usd")
    attribution_json: Mapped[dict | None] = mapped_column(JSON)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="revenue_events")


class AdvisoryNote(Base):
    """AI-generated advisory note for the business owner."""

    __tablename__ = "advisory_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    category: Mapped[str] = mapped_column(String(50))  # recommendation|warning|celebration|insight
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher = more important
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="advisory_notes")


class DistributionChannel(Base):
    """Connected distribution channel for content publishing."""

    __tablename__ = "distribution_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    platform: Mapped[str] = mapped_column(String(100))  # twitter|linkedin|email|blog
    config_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(50), default="active")  # active|paused|disconnected
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="distribution_channels")


class CompetitiveMonitor(Base):
    """Tracked competitor for ongoing competitive intelligence."""

    __tablename__ = "competitive_monitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    competitor_name: Mapped[str] = mapped_column(String(255))
    competitor_url: Mapped[str | None] = mapped_column(String(500))
    findings_json: Mapped[dict | None] = mapped_column(JSON)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="competitive_monitors")


class ContentPerformance(Base):
    """Performance metrics for published content."""

    __tablename__ = "content_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id"), index=True)
    metric_name: Mapped[str] = mapped_column(String(100))  # views|clicks|conversions|shares|engagement_rate
    value: Mapped[float] = mapped_column()
    source: Mapped[str | None] = mapped_column(String(100))  # google_analytics|manual|webhook
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    content: Mapped[Content] = relationship(back_populates="performance")


class SupportTicket(Base):
    """Support ticket — SLA and tier determined by plan."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(50), default="normal")  # normal|high|urgent
    status: Mapped[str] = mapped_column(String(50), default="open")  # open|in_progress|resolved|closed
    support_tier: Mapped[str] = mapped_column(String(50))  # standard|priority|dedicated
    response_sla_hours: Mapped[int] = mapped_column(Integer, default=24)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped["Business"] = relationship(back_populates="support_tickets")


class AdCampaign(Base):
    """Ad campaign for a business — manages targeting, budget, and platform."""

    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(50))  # google|facebook|instagram|linkedin|twitter
    campaign_type: Mapped[str] = mapped_column(String(50), default="awareness")  # awareness|traffic|conversion|retargeting
    status: Mapped[str] = mapped_column(String(50), default="draft")  # draft|review|active|paused|completed
    budget_cents: Mapped[int] = mapped_column(Integer, default=0)
    spent_cents: Mapped[int] = mapped_column(Integer, default=0)
    target_audience: Mapped[dict | None] = mapped_column(JSON)  # demographics, interests, behaviors
    schedule: Mapped[dict | None] = mapped_column(JSON)  # start_date, end_date, daily_budget
    performance: Mapped[dict | None] = mapped_column(JSON)  # impressions, clicks, conversions, cpc, ctr
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    business: Mapped[Business] = relationship(back_populates="ad_campaigns")
    ad_copies: Mapped[list["AdCopy"]] = relationship(back_populates="campaign")


class AdCopy(Base):
    """Generated ad copy variation for a campaign."""

    __tablename__ = "ad_copies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("ad_campaigns.id"), index=True)
    headline: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(String(100))  # call-to-action text
    image_prompt: Mapped[str | None] = mapped_column(Text)  # AI image gen prompt
    platform_format: Mapped[str] = mapped_column(String(50))  # single_image|carousel|video|story|text
    tone: Mapped[str] = mapped_column(String(50), default="professional")  # professional|casual|urgent|playful
    status: Mapped[str] = mapped_column(String(50), default="draft")  # draft|approved|active|rejected
    performance: Mapped[dict | None] = mapped_column(JSON)  # impressions, clicks, conversions per copy
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    campaign: Mapped[AdCampaign] = relationship(back_populates="ad_copies")


class CustomerSegment(Base):
    """AI-identified customer segment for targeted advertising."""

    __tablename__ = "customer_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    demographics: Mapped[dict | None] = mapped_column(JSON)  # age, gender, location, income
    psychographics: Mapped[dict | None] = mapped_column(JSON)  # interests, values, pain_points
    behaviors: Mapped[dict | None] = mapped_column(JSON)  # online habits, buying patterns
    estimated_size: Mapped[str | None] = mapped_column(String(100))  # e.g. "50K-100K"
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher = more valuable
    platform_targeting: Mapped[dict | None] = mapped_column(JSON)  # platform-specific targeting params
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped[Business] = relationship(back_populates="customer_segments")
