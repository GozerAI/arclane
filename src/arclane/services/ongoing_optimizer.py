"""Day 91+ ongoing optimizer — adaptive task selection for post-graduation businesses."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import (
    AdvisoryNote,
    Business,
    BusinessHealthScore,
    CompetitiveMonitor,
    Content,
    Milestone,
    RevenueEvent,
)

log = get_logger("ongoing_optimizer")

# Task templates for adaptive selection
ONGOING_TASKS = [
    {
        "key": "ongoing-content-batch",
        "area": "content",
        "action": "create_content_batch",
        "title": "Content production cycle",
        "description": (
            "This business has graduated from the 90-day program. Produce the next batch of content based on "
            "the content calendar and recent performance data. Deliverables: "
            "(1) Check the content calendar for gaps — what types haven't been published recently? "
            "(2) Produce 3-5 pieces across the best-performing channels (reference content health score). "
            "(3) For each piece: full publishable copy, not outlines. Match the brand voice guide. "
            "(4) Include 1 piece optimized for SEO using a keyword from the original SEO baseline. "
            "(5) Note which pieces should be distributed to which channels."
        ),
        "score_weight": {"content": 0.4, "momentum": 0.3},
        "cooldown_days": 3,
    },
    {
        "key": "ongoing-competitive-check",
        "area": "market_research",
        "action": "competitive_analysis",
        "title": "Competitive monitoring update",
        "description": (
            "Run a competitive monitoring check against the tracked competitors. Deliverables: "
            "(1) For each tracked competitor: check their homepage, pricing page, and recent content for changes. "
            "(2) Flag any significant shifts: new pricing, new features, new messaging, or new positioning. "
            "(3) Assess impact: does any change threaten this business's differentiation? "
            "(4) Recommend one action: messaging update, feature priority change, or positioning reinforcement. "
            "(5) Update the competitive positioning map if any competitor has moved."
        ),
        "score_weight": {"market_fit": 0.5},
        "cooldown_days": 7,
    },
    {
        "key": "ongoing-conversion-optimization",
        "area": "operations",
        "action": "optimize_conversion",
        "title": "Conversion optimization review",
        "description": (
            "Review the conversion funnel using current data and recommend optimizations. Deliverables: "
            "(1) Current funnel metrics: traffic → leads → trials → customers with conversion rates at each stage. "
            "(2) Identify the biggest drop-off point since the last review. "
            "(3) 3 specific A/B tests to run — each with variant copy and expected impact. "
            "(4) Quick win: one change to implement this week that doesn't require a test. "
            "(5) Compare current conversion rates to the targets set in the KPI definition."
        ),
        "score_weight": {"revenue": 0.4, "operations": 0.3},
        "cooldown_days": 14,
    },
    {
        "key": "ongoing-revenue-analysis",
        "area": "finance",
        "action": "analyze_revenue",
        "title": "Revenue and financial health review",
        "description": (
            "Review revenue performance and financial health. Deliverables: "
            "(1) Revenue this period vs. last period: total, by source, by channel. "
            "(2) Unit economics update: current CAC, LTV, LTV:CAC ratio, and payback period. "
            "(3) Burn rate and runway: monthly costs vs. monthly revenue, months of runway remaining. "
            "(4) ROI vs. Arclane subscription: is the subscription paying for itself? "
            "(5) One financial action item: pricing change, cost cut, or investment recommendation."
        ),
        "score_weight": {"revenue": 0.6},
        "cooldown_days": 14,
    },
    {
        "key": "ongoing-growth-experiment",
        "area": "strategy",
        "action": "design_growth_experiment",
        "title": "Growth experiment",
        "description": (
            "Design a new growth experiment based on what's working and what's stalled. Deliverables: "
            "(1) Hypothesis: 'We believe [action] will increase [metric] by [amount] because [reason].' "
            "(2) Test design: specific actions, timeline, and resource requirements. "
            "(3) Success metric and threshold. "
            "(4) Connection to prior experiments: what did the last experiment teach us? "
            "(5) If the experiment succeeds, what scales? If it fails, what's the alternative?"
        ),
        "score_weight": {"momentum": 0.4, "market_fit": 0.3},
        "cooldown_days": 14,
    },
    {
        "key": "ongoing-distribution-optimization",
        "area": "operations",
        "action": "optimize_distribution",
        "title": "Distribution channel optimization",
        "description": (
            "Review distribution channel performance and optimize. Deliverables: "
            "(1) Channel performance table: platform, posts published, engagement rate, click-through rate, "
            "leads generated. (2) Best performing channel and content type combination. "
            "(3) Underperforming channels: continue, adjust, or pause? With specific reasoning. "
            "(4) Publishing schedule optimization: should frequency change for any channel? "
            "(5) One new distribution tactic to test in the next 7 days."
        ),
        "score_weight": {"content": 0.3, "operations": 0.3},
        "cooldown_days": 7,
    },
    {
        "key": "ongoing-brand-refresh",
        "area": "content",
        "action": "refresh_brand_content",
        "title": "Brand content refresh",
        "description": (
            "Update key brand assets based on what's been learned since the last refresh. Deliverables: "
            "(1) Landing page audit: review current copy against latest positioning and customer feedback. "
            "Rewrite any sections that feel outdated. (2) Messaging check: do the homepage, social bios, "
            "and email templates still reflect the current offer? Update any that don't. "
            "(3) Social proof update: add any new testimonials, metrics, or case studies. "
            "(4) CTA optimization: is the primary CTA still the right one? Recommend changes if needed."
        ),
        "score_weight": {"market_fit": 0.3, "content": 0.3},
        "cooldown_days": 21,
    },
    {
        "key": "ongoing-partnership-outreach",
        "area": "market_research",
        "action": "identify_partners",
        "title": "Partnership and outreach cycle",
        "description": (
            "Identify new partnership opportunities and refresh outreach. Deliverables: "
            "(1) 3 new potential partners: name, why they're a fit, what the partnership looks like. "
            "(2) Status update on existing partnership leads: any responses, meetings scheduled, or deals closed? "
            "(3) For each new partner: one-sentence outreach hook and recommended contact channel. "
            "(4) One co-marketing idea that could be executed in the next 30 days."
        ),
        "score_weight": {"market_fit": 0.3, "momentum": 0.3},
        "cooldown_days": 14,
    },
    {
        "key": "ongoing-retention-review",
        "area": "operations",
        "action": "review_retention",
        "title": "Customer retention review",
        "description": (
            "Analyze retention and customer health. Deliverables: "
            "(1) Churn rate: current rate and trend vs. previous period. "
            "(2) At-risk customers: identify patterns that predict churn (inactivity, support tickets, etc.). "
            "(3) Engagement analysis: which onboarding steps correlate with long-term retention? "
            "(4) 3 specific retention interventions to implement or update. "
            "(5) NPS or satisfaction signal: any customer feedback that should influence product decisions?"
        ),
        "score_weight": {"revenue": 0.4, "operations": 0.3},
        "cooldown_days": 14,
    },
    {
        "key": "ongoing-quarterly-plan",
        "area": "strategy",
        "action": "create_quarterly_plan",
        "title": "Quarterly planning cycle",
        "description": (
            "Quarterly review and planning. This runs every ~90 days post-graduation. Deliverables: "
            "(1) Quarter in review: what worked, what didn't, key metrics vs. targets. "
            "(2) Top 3 learnings that should change how the business operates. "
            "(3) Next quarter priorities: the 3 highest-ROI activities with monthly milestones. "
            "(4) Channels to double down on vs. channels to cut. "
            "(5) Resource allocation: budget, time, and hiring recommendations. "
            "(6) Updated financial projection: based on current trajectory, where will the business "
            "be in 90 days?"
        ),
        "score_weight": {"momentum": 0.5},
        "cooldown_days": 80,
    },
]


async def select_adaptive_task(business: Business, session: AsyncSession) -> dict | None:
    """Select the highest-impact task for tonight's cycle based on health scores and history.

    The algorithm:
    1. Get the latest health sub-scores
    2. For each task template, calculate a priority score based on:
       - How much the task addresses the weakest health areas (inverse score weighting)
       - Whether the cooldown period has elapsed since last execution
    3. Return the highest-priority eligible task
    """
    # Get latest health scores
    health_scores = await _get_latest_health_scores(business, session)
    if not health_scores:
        # No health data yet — record an initial snapshot
        try:
            from arclane.services.health_score_service import record_health_snapshot
            await record_health_snapshot(business, session)
            health_scores = await _get_latest_health_scores(business, session)
        except Exception:
            log.warning("Could not bootstrap health scores for %s", business.slug)

    # Get recent cycle task keys to enforce cooldowns
    recent_keys = await _get_recent_task_keys(business, session, days=90)

    scored_tasks = []
    for task_template in ONGOING_TASKS:
        # Check cooldown
        cooldown_days = task_template.get("cooldown_days", 7)
        last_run = recent_keys.get(task_template["key"])
        if last_run:
            days_since = (datetime.now(timezone.utc) - last_run).days
            if days_since < cooldown_days:
                continue

        # Calculate priority based on health score gaps + content performance
        priority = _calculate_task_priority(task_template, health_scores)

        # Boost content tasks if content health is low, deprioritize if high
        if task_template["area"] == "content":
            content_score = health_scores.get("content", 50.0)
            if content_score < 30:
                priority *= 1.3  # Urgent: content production stalled
            elif content_score > 80:
                priority *= 0.7  # Content is strong — focus elsewhere

        scored_tasks.append((priority, task_template))

    if not scored_tasks:
        # All tasks on cooldown — fall back to content production
        return _build_task_dict(business, ONGOING_TASKS[0])

    # Sort by priority (highest first)
    scored_tasks.sort(key=lambda x: x[0], reverse=True)
    best = scored_tasks[0][1]

    log.info(
        "Adaptive task selected for %s: %s (priority=%.1f)",
        business.slug, best["title"], scored_tasks[0][0],
    )
    return _build_task_dict(business, best)


def _calculate_task_priority(task_template: dict, health_scores: dict) -> float:
    """Calculate priority score for a task based on health score gaps.

    Lower health scores in weighted areas = higher priority for that task.
    """
    weights = task_template.get("score_weight", {})
    if not weights:
        return 50.0

    priority = 0.0
    for score_type, weight in weights.items():
        current_score = health_scores.get(score_type, 50.0)
        # Inverse: lower health = higher priority (100 - score gives gap)
        gap = 100.0 - current_score
        priority += gap * weight

    return priority


async def _get_latest_health_scores(business: Business, session: AsyncSession) -> dict:
    """Get the most recent health sub-scores."""
    result = await session.execute(
        select(BusinessHealthScore.score_type, BusinessHealthScore.score)
        .where(BusinessHealthScore.business_id == business.id)
        .order_by(BusinessHealthScore.recorded_at.desc())
        .limit(10)
    )
    scores = {}
    for score_type, score in result.all():
        if score_type not in scores:  # Keep only the latest per type
            scores[score_type] = score
    return scores


async def _get_recent_task_keys(business: Business, session: AsyncSession, days: int = 90) -> dict:
    """Get task keys and their last execution dates from recent cycles."""
    from arclane.models.tables import Cycle

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(Cycle).where(
            Cycle.business_id == business.id,
            Cycle.status == "completed",
            Cycle.created_at >= cutoff,
        ).order_by(Cycle.created_at.desc())
    )
    cycles = result.scalars().all()

    key_dates: dict[str, datetime] = {}
    for cycle in cycles:
        cycle_result = cycle.result or {}
        for task_result in cycle_result.get("results", []):
            key = task_result.get("queue_task_key") or task_result.get("key", "")
            if key and key not in key_dates:
                key_dates[key] = cycle.created_at

    return key_dates


def _build_task_dict(business: Business, template: dict) -> dict:
    """Build a full task dict from a template."""
    context_suffix = ""
    if business.website_summary:
        context_suffix = f" Existing site context: {business.website_summary}"
    elif business.website_url:
        context_suffix = f" Existing site URL: {business.website_url}"

    return {
        "key": template["key"],
        "output_key": template["key"],
        "kind": "ongoing",
        "area": template["area"],
        "action": template["action"],
        "title": template["title"],
        "status_label": f"Forever Partner: {template['title']}",
        "brief": template["description"],
        "description": f"{template['description']} Business context: {business.description}.{context_suffix}",
        "expected_output": template["title"],
        "depends_on": [],
        "queue_status": "pending",
        "duration_days": 1,
        "days_remaining": 1,
        "working_days_required": 1,
        "queue_task_key": template["key"],
        "phase": 5,
    }
