"""Upsell, engagement, and retention routes — wires the UpsellEngine to the API."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business, Content, Cycle

router = APIRouter()

# Singleton engine
_engine = UpsellEngine()


def _behavior_from_db(business: Business, cycles_run: int, content_created: int) -> UserBehavior:
    """Build a UserBehavior from DB state for the upsell engine."""
    plan_info_working_days = {"starter": 5, "pro": 20, "growth": 60, "scale": 150, "enterprise": 999}
    total = plan_info_working_days.get(business.plan, 5)
    used = max(0, total - business.working_days_remaining)
    return UserBehavior(
        business_id=business.id,
        plan=business.plan,
        working_days_used=used,
        working_days_total=total,
        cycles_run=cycles_run,
        content_created=content_created,
        last_active_at=business.updated_at,
        signup_at=business.created_at,
    )


async def _ensure_behavior(business: Business, session: AsyncSession) -> UserBehavior:
    """Load or create tracked behavior for a business."""
    existing = _engine.get_behavior(business.id)
    if existing:
        return existing

    cycles_run = (await session.execute(
        select(func.count(Cycle.id)).where(
            Cycle.business_id == business.id, Cycle.status == "completed",
        )
    )).scalar() or 0

    content_created = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0

    beh = _behavior_from_db(business, cycles_run, content_created)
    _engine.track_behavior(beh)
    return beh


# --- Engagement score ---


class EngagementScoreResponse(BaseModel):
    score: float
    level: str
    factors: dict[str, float]
    triggers: list[str]


@router.get("/engagement", response_model=EngagementScoreResponse)
async def engagement_score(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Compute engagement score and recommended triggers."""
    await _ensure_behavior(business, session)
    score = _engine.compute_engagement_score(business.id)
    return EngagementScoreResponse(
        score=score.score, level=score.level,
        factors=score.factors, triggers=score.triggers,
    )


# --- Upgrade prompts ---


class UpgradePromptResponse(BaseModel):
    prompt_type: str
    current_plan: str
    suggested_plan: str | None
    message: str
    cta_text: str
    cta_url: str
    priority: int


@router.get("/prompts", response_model=list[UpgradePromptResponse])
async def get_upgrade_prompts(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get all applicable upgrade prompts for the business."""
    await _ensure_behavior(business, session)
    prompts = []

    limit_prompt = _engine.generate_limit_prompt(business.id)
    if limit_prompt:
        prompts.append(limit_prompt)

    milestone = _engine.check_milestones(business.id)
    if milestone:
        prompts.append(milestone)

    ctas = _engine.generate_contextual_cta(business.id)
    prompts.extend(ctas)

    prompts.sort(key=lambda p: p.priority, reverse=True)
    return [
        UpgradePromptResponse(
            prompt_type=p.prompt_type, current_plan=p.current_plan,
            suggested_plan=p.suggested_plan, message=p.message,
            cta_text=p.cta_text, cta_url=p.cta_url, priority=p.priority,
        )
        for p in prompts[:5]  # cap at 5 prompts
    ]


# --- Feature spotlights ---


@router.get("/spotlights", response_model=list[UpgradePromptResponse])
async def feature_spotlights(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get feature spotlight suggestions for unused plan features."""
    await _ensure_behavior(business, session)
    spots = _engine.generate_feature_spotlights(business.id)
    return [
        UpgradePromptResponse(
            prompt_type=p.prompt_type, current_plan=p.current_plan,
            suggested_plan=p.suggested_plan, message=p.message,
            cta_text=p.cta_text, cta_url=p.cta_url, priority=p.priority,
        )
        for p in spots
    ]


# --- Demo sessions ---


class DemoStartRequest(BaseModel):
    feature: str
    duration_hours: int = 24
    max_actions: int = 10


class DemoResponse(BaseModel):
    feature: str
    actions_taken: int
    max_actions: int
    expires_at: str


@router.post("/demos", response_model=DemoResponse)
async def start_demo(
    payload: DemoStartRequest,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Start a time-limited demo of a premium feature."""
    await _ensure_behavior(business, session)
    try:
        demo = _engine.start_demo(
            business.id, payload.feature,
            payload.duration_hours, payload.max_actions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return DemoResponse(
        feature=demo.feature, actions_taken=demo.actions_taken,
        max_actions=demo.max_actions, expires_at=demo.expires_at.isoformat(),
    )


@router.get("/demos", response_model=list[DemoResponse])
async def list_active_demos(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List active demo sessions."""
    await _ensure_behavior(business, session)
    demos = _engine.get_active_demos(business.id)
    return [
        DemoResponse(
            feature=d.feature, actions_taken=d.actions_taken,
            max_actions=d.max_actions, expires_at=d.expires_at.isoformat(),
        )
        for d in demos
    ]


# --- Product tour ---


class TourStepResponse(BaseModel):
    step_id: str
    title: str
    description: str
    target_element: str
    action: str
    completed: bool


class TourResponse(BaseModel):
    current_step: int
    total_steps: int
    completed: bool
    steps: list[TourStepResponse]


@router.post("/tour", response_model=TourResponse)
async def start_tour(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Start the product onboarding tour."""
    await _ensure_behavior(business, session)
    tour = _engine.create_product_tour(business.id)
    return _tour_response(tour)


@router.post("/tour/advance", response_model=TourResponse)
async def advance_tour(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Advance to the next tour step."""
    await _ensure_behavior(business, session)
    tour = _engine.advance_tour(business.id)
    if not tour:
        raise HTTPException(status_code=404, detail="No active tour")
    return _tour_response(tour)


def _tour_response(tour) -> TourResponse:
    return TourResponse(
        current_step=tour.current_step,
        total_steps=len(tour.steps),
        completed=tour.completed_at is not None,
        steps=[
            TourStepResponse(
                step_id=s.step_id, title=s.title, description=s.description,
                target_element=s.target_element, action=s.action,
                completed=s.completed,
            )
            for s in tour.steps
        ],
    )


# --- Satisfaction survey ---


class SurveyResponse(BaseModel):
    questions: list[dict]


@router.get("/survey", response_model=SurveyResponse)
async def get_survey(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get the satisfaction survey questions."""
    await _ensure_behavior(business, session)
    survey = _engine.create_satisfaction_survey(business.id)
    return SurveyResponse(questions=survey["questions"])


class SurveySubmission(BaseModel):
    responses: dict


@router.post("/survey")
async def submit_survey(
    payload: SurveySubmission,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Submit survey responses and get triggered actions."""
    await _ensure_behavior(business, session)
    result = _engine.process_survey_response(business.id, payload.responses)
    return result


# --- Phase-aware suggestions ---


@router.get("/phase-suggestions")
async def phase_suggestions(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get upsell suggestions tailored to the current roadmap phase."""
    phase = getattr(business, "current_phase", 0) or 0
    day = getattr(business, "roadmap_day", 0) or 0

    suggestions = _PHASE_SUGGESTIONS.get(phase, [])

    # Filter out suggestions for plans they already have
    current_plan = business.plan
    plan_rank = {"preview": 0, "starter": 1, "pro": 2, "growth": 3, "scale": 4}
    current_rank = plan_rank.get(current_plan, 0)

    filtered = []
    for s in suggestions:
        min_rank = plan_rank.get(s.get("min_plan", "starter"), 0)
        if current_rank < min_rank:
            filtered.append({**s, "current_plan": current_plan, "roadmap_day": day})

    # Add working-day-based suggestion if low
    total_working_days = business.working_days_remaining + business.working_days_bonus
    if total_working_days <= 5 and phase <= 4:
        days_in_phase = {1: 21, 2: 24, 3: 30, 4: 15}.get(phase, 20)
        working_days_needed = max(0, days_in_phase - total_working_days)
        if working_days_needed > 0:
            filtered.insert(0, {
                "type": "working_day_warning",
                "title": f"You need ~{working_days_needed} more working days to finish Phase {phase}",
                "message": f"At 1 working day per night, you'll run out before Phase {phase} ends. Upgrade to keep momentum.",
                "cta_text": "View Plans",
                "cta_url": "/billing",
                "priority": 9,
            })

    return {"phase": phase, "day": day, "suggestions": filtered[:5]}


_PHASE_SUGGESTIONS = {
    1: [
        {"type": "add_on", "title": "Deep Market Dive", "message": "Go deeper on your market research while it's fresh.", "cta_text": "Add for $119", "cta_url": "/billing/add-on/deep-market-dive", "priority": 6, "min_plan": "starter"},
        {"type": "add_on", "title": "Landing Page Sprint", "message": "Turn your draft into a high-converting page.", "cta_text": "Add for $89", "cta_url": "/billing/add-on/landing-page-sprint", "priority": 5, "min_plan": "starter"},
    ],
    2: [
        {"type": "upgrade", "title": "Upgrade to Pro", "message": "Phase 2 needs 12+ working days. Pro gives you 20/month.", "cta_text": "Upgrade to Pro — $99/mo", "cta_url": "/billing/upgrade/pro", "priority": 8, "min_plan": "pro"},
        {"type": "add_on", "title": "Social Batch Pack", "message": "Scale your content production with a 6-piece batch.", "cta_text": "Add for $69", "cta_url": "/billing/add-on/social-batch-pack", "priority": 5, "min_plan": "starter"},
    ],
    3: [
        {"type": "upgrade", "title": "Upgrade to Growth", "message": "Scaling content + revenue tracking needs Growth plan working days.", "cta_text": "Upgrade to Growth — $249/mo", "cta_url": "/billing/upgrade/growth", "priority": 8, "min_plan": "growth"},
        {"type": "add_on", "title": "Competitor Teardown", "message": "Know exactly where competitors are weak before you scale.", "cta_text": "Add for $79", "cta_url": "/billing/add-on/expanded-competitor-teardown", "priority": 6, "min_plan": "starter"},
    ],
    4: [
        {"type": "upgrade", "title": "Upgrade to Scale", "message": "Post-graduation 'Forever Partner' mode needs Scale plan capacity.", "cta_text": "Upgrade to Scale — $499/mo", "cta_url": "/billing/upgrade/scale", "priority": 8, "min_plan": "scale"},
    ],
    5: [
        {"type": "upgrade", "title": "Scale for Growth", "message": "Forever Partner mode runs best with 150 working days/month.", "cta_text": "Upgrade to Scale", "cta_url": "/billing/upgrade/scale", "priority": 7, "min_plan": "scale"},
    ],
}


# --- Win-back (for churned businesses) ---


@router.get("/winback", response_model=UpgradePromptResponse | None)
async def winback_offer(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get a win-back offer if the business is churned."""
    beh = await _ensure_behavior(business, session)
    if business.plan == "cancelled":
        updated = business.updated_at
        if updated and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        beh.churned_at = updated
    prompt = _engine.generate_winback(business.id)
    if not prompt:
        return None
    return UpgradePromptResponse(
        prompt_type=prompt.prompt_type, current_plan=prompt.current_plan,
        suggested_plan=prompt.suggested_plan, message=prompt.message,
        cta_text=prompt.cta_text, cta_url=prompt.cta_url, priority=prompt.priority,
    )
