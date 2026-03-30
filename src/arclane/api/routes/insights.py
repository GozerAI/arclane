"""Insights routes — LTV prediction, onboarding funnel, A/B testing,
journey analytics. Wires the AnalyticsEngine to the API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.analytics.engine import AnalyticsEngine
from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business, Content, Cycle

router = APIRouter()

_engine = AnalyticsEngine()


# --- LTV prediction ---


class LTVRequest(BaseModel):
    monthly_revenue_cents: int = 4900
    engagement_score: float = 50.0
    cycles_per_month: float = 5.0
    churn_signals: int = 0


class LTVResponse(BaseModel):
    predicted_ltv_cents: int
    confidence: float
    predicted_months_remaining: int
    churn_probability: float
    expansion_probability: float
    factors: dict[str, float]


@router.post("/ltv", response_model=LTVResponse)
async def predict_ltv(
    payload: LTVRequest,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Predict customer lifetime value for a business."""
    months_active = 1
    first_cycle = (await session.execute(
        select(Cycle.created_at).where(Cycle.business_id == business.id)
        .order_by(Cycle.created_at.asc()).limit(1)
    )).scalar()
    if first_cycle:
        fc = first_cycle if first_cycle.tzinfo else first_cycle.replace(tzinfo=timezone.utc)
        months_active = max(1, int(
            (datetime.now(timezone.utc) - fc).total_seconds() / (30 * 86400)
        ))

    prediction = _engine.predict_ltv(
        business.id,
        monthly_revenue_cents=payload.monthly_revenue_cents,
        months_active=months_active,
        engagement_score=payload.engagement_score,
        cycles_per_month=payload.cycles_per_month,
        churn_signals=payload.churn_signals,
    )
    return LTVResponse(
        predicted_ltv_cents=prediction.predicted_ltv_cents,
        confidence=prediction.confidence,
        predicted_months_remaining=prediction.predicted_months_remaining,
        churn_probability=prediction.churn_probability,
        expansion_probability=prediction.expansion_probability,
        factors=prediction.factors,
    )


# --- Onboarding funnel ---


class OnboardingFunnelResponse(BaseModel):
    total_signups: int
    completed_profile: int
    first_cycle: int
    first_content: int
    paid_conversion: int
    stage_rates: dict[str, float]
    avg_time_to_activation_hours: float
    drop_off_stages: dict[str, int]


@router.get("/onboarding-funnel", response_model=OnboardingFunnelResponse)
async def onboarding_funnel(
    session: AsyncSession = Depends(get_session),
):
    """Get the onboarding funnel analytics (platform-wide).

    Auto-populates from DB state if the engine hasn't been seeded.
    """
    # Seed from DB if empty
    businesses = (await session.execute(select(Business))).scalars().all()
    for biz in businesses:
        created = biz.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        _engine.record_onboarding_event(biz.id, "signup", created)

        has_cycle = (await session.execute(
            select(Cycle.id).where(Cycle.business_id == biz.id).limit(1)
        )).scalar()
        if has_cycle:
            _engine.record_onboarding_event(biz.id, "first_cycle")

        has_content = (await session.execute(
            select(Content.id).where(Content.business_id == biz.id).limit(1)
        )).scalar()
        if has_content:
            _engine.record_onboarding_event(biz.id, "first_content")

        if biz.plan not in ("preview", "cancelled"):
            _engine.record_onboarding_event(biz.id, "paid_conversion")

    funnel = _engine.get_onboarding_funnel()
    return OnboardingFunnelResponse(
        total_signups=funnel.total_signups,
        completed_profile=funnel.completed_profile,
        first_cycle=funnel.first_cycle,
        first_content=funnel.first_content,
        paid_conversion=funnel.paid_conversion,
        stage_rates=funnel.stage_rates,
        avg_time_to_activation_hours=funnel.avg_time_to_activation_hours,
        drop_off_stages=funnel.drop_off_stages,
    )


# --- A/B testing ---


class ExperimentCreateRequest(BaseModel):
    name: str
    description: str
    variants: list[dict]
    metric: str = "conversion_rate"


class ExperimentResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    metric: str
    variants: list[dict]
    traffic_split: dict[str, float]
    winner: str | None = None


@router.post("/experiments", response_model=ExperimentResponse)
async def create_experiment(payload: ExperimentCreateRequest):
    """Create a new A/B experiment."""
    if len(payload.variants) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 variants")
    exp = _engine.create_experiment(
        payload.name, payload.description,
        payload.variants, payload.metric,
    )
    return _exp_response(exp)


@router.post("/experiments/{experiment_id}/start", response_model=ExperimentResponse)
async def start_experiment(experiment_id: str):
    """Start a draft experiment."""
    try:
        exp = _engine.start_experiment(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _exp_response(exp)


@router.post("/experiments/{experiment_id}/assign")
async def assign_variant(
    experiment_id: str,
    business: Business = Depends(get_business),
):
    """Assign a business to an experiment variant."""
    try:
        assignment = _engine.assign_variant(experiment_id, business.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "experiment_id": assignment.experiment_id,
        "business_id": assignment.business_id,
        "variant": assignment.variant,
    }


@router.post("/experiments/{experiment_id}/convert")
async def record_conversion(
    experiment_id: str,
    business: Business = Depends(get_business),
):
    """Record a conversion for a business in an experiment."""
    assignment = _engine.record_conversion(experiment_id, business.id)
    if not assignment:
        raise HTTPException(status_code=404, detail="No assignment found")
    return {"converted": True, "variant": assignment.variant}


@router.get("/experiments/{experiment_id}/results")
async def experiment_results(experiment_id: str):
    """Get results for an experiment."""
    try:
        return _engine.get_experiment_results(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/experiments/{experiment_id}/complete", response_model=ExperimentResponse)
async def complete_experiment(experiment_id: str):
    """Mark an experiment as completed."""
    try:
        exp = _engine.complete_experiment(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _exp_response(exp)


def _exp_response(exp) -> ExperimentResponse:
    return ExperimentResponse(
        id=exp.id, name=exp.name, description=exp.description,
        status=exp.status, metric=exp.metric,
        variants=exp.variants, traffic_split=exp.traffic_split,
        winner=exp.winner,
    )


# --- Journey analytics ---


@router.get("/journeys")
async def journey_analytics():
    """Get aggregate journey analytics across all businesses."""
    return _engine.get_journey_analytics()


@router.get("/journeys/{business_id}")
async def get_journey(business_id: int):
    """Get the journey map for a specific business."""
    journey = _engine.get_journey(business_id)
    if not journey:
        return {"business_id": business_id, "events": [], "current_stage": None}
    return {
        "business_id": journey.business_id,
        "current_stage": journey.current_stage,
        "events": [
            {"stage": e.stage, "action": e.action,
             "timestamp": e.timestamp.isoformat()}
            for e in journey.events
        ],
        "stage_durations": journey.stage_durations,
    }
