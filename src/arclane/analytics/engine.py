"""Analytics engine: customer insights dashboard, segmentation, journey mapping,
LTV prediction, referral analytics, onboarding funnel, A/B testing framework.

Covers: 471,475,479,483,487,491,496.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SegmentType(str, Enum):
    PLAN = "plan"
    ENGAGEMENT = "engagement"
    REVENUE = "revenue"
    LIFECYCLE = "lifecycle"
    CUSTOM = "custom"


class JourneyStage(str, Enum):
    AWARENESS = "awareness"
    SIGNUP = "signup"
    ONBOARDING = "onboarding"
    ACTIVATION = "activation"
    ENGAGEMENT = "engagement"
    CONVERSION = "conversion"
    RETENTION = "retention"
    EXPANSION = "expansion"
    ADVOCACY = "advocacy"
    CHURN = "churn"


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CustomerInsight:
    business_id: int
    plan: str
    lifetime_value_cents: int
    months_active: int
    total_cycles: int
    total_content: int
    features_used: int
    engagement_score: float
    churn_risk: float
    expansion_potential: float
    last_active_at: datetime | None = None
    segments: list[str] = field(default_factory=list)


@dataclass
class CustomerSegment:
    id: str
    name: str
    segment_type: str
    criteria: dict[str, Any] = field(default_factory=dict)
    member_count: int = 0
    avg_ltv_cents: int = 0
    avg_engagement: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class JourneyEvent:
    business_id: int
    stage: str
    action: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class JourneyMap:
    business_id: int
    events: list[JourneyEvent] = field(default_factory=list)
    current_stage: str = "signup"
    stage_durations: dict[str, float] = field(default_factory=dict)
    drop_off_risk: float = 0.0


@dataclass
class LTVPrediction:
    business_id: int
    predicted_ltv_cents: int
    confidence: float
    factors: dict[str, float] = field(default_factory=dict)
    predicted_months_remaining: int = 0
    churn_probability: float = 0.0
    expansion_probability: float = 0.0


@dataclass
class ReferralAnalytics:
    total_referrals: int = 0
    converted_referrals: int = 0
    conversion_rate: float = 0.0
    total_revenue_from_referrals_cents: int = 0
    avg_referral_ltv_cents: int = 0
    top_referrers: list[dict[str, Any]] = field(default_factory=list)
    referral_by_channel: dict[str, int] = field(default_factory=dict)


@dataclass
class OnboardingFunnel:
    total_signups: int = 0
    completed_profile: int = 0
    first_cycle: int = 0
    first_content: int = 0
    paid_conversion: int = 0
    stage_rates: dict[str, float] = field(default_factory=dict)
    avg_time_to_activation_hours: float = 0.0
    drop_off_stages: dict[str, int] = field(default_factory=dict)


@dataclass
class ABExperiment:
    id: str
    name: str
    description: str
    status: str = "draft"
    variants: list[dict[str, Any]] = field(default_factory=list)
    traffic_split: dict[str, float] = field(default_factory=dict)
    metric: str = "conversion_rate"
    start_at: datetime | None = None
    end_at: datetime | None = None
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    winner: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ABAssignment:
    experiment_id: str
    business_id: int
    variant: str
    converted: bool = False
    metric_value: float = 0.0
    assigned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Analytics Engine
# ---------------------------------------------------------------------------

class AnalyticsEngine:
    """Customer analytics: segmentation, journey mapping, LTV, referral tracking,
    onboarding funnels, and A/B testing."""

    def __init__(self) -> None:
        self._insights: dict[int, CustomerInsight] = {}
        self._segments: dict[str, CustomerSegment] = {}
        self._journeys: dict[int, JourneyMap] = {}
        self._experiments: dict[str, ABExperiment] = {}
        self._assignments: list[ABAssignment] = []
        self._referral_events: list[dict[str, Any]] = []
        self._onboarding_events: list[dict[str, Any]] = []

    # -- customer insights dashboard (471) --

    def record_insight(self, insight: CustomerInsight) -> CustomerInsight:
        self._insights[insight.business_id] = insight
        return insight

    def get_insight(self, business_id: int) -> CustomerInsight | None:
        return self._insights.get(business_id)

    def get_insights_summary(self) -> dict[str, Any]:
        if not self._insights:
            return {
                "total_customers": 0, "avg_ltv_cents": 0,
                "avg_engagement": 0.0, "high_churn_risk": 0,
                "expansion_candidates": 0,
            }
        insights = list(self._insights.values())
        total = len(insights)
        avg_ltv = sum(i.lifetime_value_cents for i in insights) // max(total, 1)
        avg_eng = sum(i.engagement_score for i in insights) / max(total, 1)
        high_churn = len([i for i in insights if i.churn_risk > 0.7])
        expansion = len([i for i in insights if i.expansion_potential > 0.5])
        return {
            "total_customers": total,
            "avg_ltv_cents": avg_ltv,
            "avg_engagement": round(avg_eng, 2),
            "high_churn_risk": high_churn,
            "expansion_candidates": expansion,
        }

    # -- customer segmentation engine (475) --

    def create_segment(
        self, name: str, segment_type: str,
        criteria: dict[str, Any],
    ) -> CustomerSegment:
        seg_id = hashlib.sha256(
            f"{name}-{time.time()}".encode()
        ).hexdigest()[:12]
        segment = CustomerSegment(
            id=seg_id, name=name, segment_type=segment_type,
            criteria=criteria,
        )
        self._segments[seg_id] = segment
        return segment

    def evaluate_segment(self, segment_id: str) -> CustomerSegment:
        seg = self._segments.get(segment_id)
        if not seg:
            raise ValueError(f"Segment {segment_id} not found")
        members: list[CustomerInsight] = []
        for insight in self._insights.values():
            if self._matches_criteria(insight, seg.criteria):
                members.append(insight)
                if seg.id not in insight.segments:
                    insight.segments.append(seg.id)
        seg.member_count = len(members)
        if members:
            seg.avg_ltv_cents = sum(
                m.lifetime_value_cents for m in members) // len(members)
            seg.avg_engagement = round(
                sum(m.engagement_score for m in members) / len(members), 2)
        return seg

    def get_segments(self) -> list[CustomerSegment]:
        return list(self._segments.values())

    def get_segment_members(self, segment_id: str) -> list[CustomerInsight]:
        seg = self._segments.get(segment_id)
        if not seg:
            return []
        return [i for i in self._insights.values()
                if self._matches_criteria(i, seg.criteria)]

    # -- customer journey mapping (479) --

    def record_journey_event(
        self, business_id: int, stage: str, action: str,
        metadata: dict[str, Any] | None = None,
    ) -> JourneyMap:
        if business_id not in self._journeys:
            self._journeys[business_id] = JourneyMap(business_id=business_id)
        journey = self._journeys[business_id]
        event = JourneyEvent(
            business_id=business_id, stage=stage,
            action=action, metadata=metadata or {},
        )
        journey.events.append(event)
        journey.current_stage = stage
        # compute stage durations
        self._compute_stage_durations(journey)
        return journey

    def get_journey(self, business_id: int) -> JourneyMap | None:
        return self._journeys.get(business_id)

    def get_journey_analytics(self) -> dict[str, Any]:
        if not self._journeys:
            return {"total_journeys": 0, "stage_distribution": {},
                    "avg_durations": {}}
        stage_counts: dict[str, int] = {}
        stage_durations: dict[str, list[float]] = {}
        for j in self._journeys.values():
            stage_counts[j.current_stage] = (
                stage_counts.get(j.current_stage, 0) + 1)
            for stage, dur in j.stage_durations.items():
                if stage not in stage_durations:
                    stage_durations[stage] = []
                stage_durations[stage].append(dur)
        avg_durations = {
            s: round(sum(ds) / len(ds), 2)
            for s, ds in stage_durations.items() if ds
        }
        return {
            "total_journeys": len(self._journeys),
            "stage_distribution": stage_counts,
            "avg_durations": avg_durations,
        }

    # -- customer lifetime value prediction (483) --

    def predict_ltv(
        self, business_id: int,
        monthly_revenue_cents: int = 0,
        months_active: int = 1,
        engagement_score: float = 50.0,
        cycles_per_month: float = 5.0,
        churn_signals: int = 0,
    ) -> LTVPrediction:
        # base survival probability
        base_retention = 0.95 - (churn_signals * 0.05)
        base_retention = max(0.3, min(0.99, base_retention))
        # engagement factor
        eng_factor = min(engagement_score / 100, 1.0)
        retention_rate = base_retention * (0.5 + 0.5 * eng_factor)
        # tenure factor (longer tenure = less likely to churn)
        tenure_factor = min(1.0, 0.7 + months_active * 0.03)
        monthly_churn = 1 - (retention_rate * tenure_factor)
        monthly_churn = max(0.01, min(0.5, monthly_churn))
        # predicted remaining months
        predicted_months = round(1 / monthly_churn)
        predicted_months = min(predicted_months, 60)
        # expansion probability
        if engagement_score > 70 and cycles_per_month > 10:
            expansion_prob = 0.6
        elif engagement_score > 50:
            expansion_prob = 0.3
        else:
            expansion_prob = 0.1
        # predicted LTV
        base_ltv = monthly_revenue_cents * predicted_months
        expansion_boost = round(base_ltv * expansion_prob * 0.3)
        predicted_ltv = base_ltv + expansion_boost
        factors = {
            "retention_rate": round(retention_rate, 3),
            "tenure_factor": round(tenure_factor, 3),
            "engagement_factor": round(eng_factor, 3),
            "monthly_churn": round(monthly_churn, 3),
            "expansion_boost_cents": expansion_boost,
        }
        confidence = min(0.95, 0.4 + months_active * 0.05 + eng_factor * 0.2)
        return LTVPrediction(
            business_id=business_id,
            predicted_ltv_cents=predicted_ltv,
            confidence=round(confidence, 2),
            factors=factors,
            predicted_months_remaining=predicted_months,
            churn_probability=round(monthly_churn, 3),
            expansion_probability=round(expansion_prob, 2),
        )

    # -- referral program analytics (487) --

    def record_referral_event(
        self, referrer_business_id: int, referred_business_id: int,
        channel: str = "direct", converted: bool = False,
        revenue_cents: int = 0,
    ) -> None:
        self._referral_events.append({
            "referrer": referrer_business_id,
            "referred": referred_business_id,
            "channel": channel,
            "converted": converted,
            "revenue_cents": revenue_cents,
            "timestamp": datetime.now(timezone.utc),
        })

    def get_referral_analytics(self) -> ReferralAnalytics:
        events = self._referral_events
        if not events:
            return ReferralAnalytics()
        total = len(events)
        converted = [e for e in events if e["converted"]]
        total_revenue = sum(e["revenue_cents"] for e in converted)
        # top referrers
        referrer_counts: dict[int, int] = {}
        for e in events:
            r = e["referrer"]
            referrer_counts[r] = referrer_counts.get(r, 0) + 1
        top = sorted(referrer_counts.items(), key=lambda x: x[1], reverse=True)
        top_referrers = [
            {"business_id": bid, "referrals": count}
            for bid, count in top[:10]
        ]
        # by channel
        by_channel: dict[str, int] = {}
        for e in events:
            ch = e["channel"]
            by_channel[ch] = by_channel.get(ch, 0) + 1
        return ReferralAnalytics(
            total_referrals=total,
            converted_referrals=len(converted),
            conversion_rate=round(len(converted) / max(total, 1) * 100, 1),
            total_revenue_from_referrals_cents=total_revenue,
            avg_referral_ltv_cents=total_revenue // max(len(converted), 1),
            top_referrers=top_referrers,
            referral_by_channel=by_channel,
        )

    # -- user onboarding funnel analytics (491) --

    def record_onboarding_event(
        self, business_id: int, stage: str,
        timestamp: datetime | None = None,
    ) -> None:
        self._onboarding_events.append({
            "business_id": business_id,
            "stage": stage,
            "timestamp": timestamp or datetime.now(timezone.utc),
        })

    def get_onboarding_funnel(self) -> OnboardingFunnel:
        events = self._onboarding_events
        if not events:
            return OnboardingFunnel()
        businesses: dict[int, set[str]] = {}
        for e in events:
            bid = e["business_id"]
            if bid not in businesses:
                businesses[bid] = set()
            businesses[bid].add(e["stage"])
        total = len(businesses)
        stages = ["signup", "profile_complete", "first_cycle",
                  "first_content", "paid_conversion"]
        counts = {s: 0 for s in stages}
        for stages_set in businesses.values():
            for s in stages:
                if s in stages_set:
                    counts[s] += 1
        rates = {s: round(counts[s] / max(total, 1) * 100, 1) for s in stages}
        # compute drop-offs
        drop_offs: dict[str, int] = {}
        for i in range(len(stages) - 1):
            drop = counts[stages[i]] - counts[stages[i + 1]]
            drop_offs[f"{stages[i]}_to_{stages[i+1]}"] = max(0, drop)
        # avg time to activation (signup -> first_cycle)
        activation_times: list[float] = []
        biz_events: dict[int, dict[str, datetime]] = {}
        for e in events:
            bid = e["business_id"]
            if bid not in biz_events:
                biz_events[bid] = {}
            biz_events[bid][e["stage"]] = e["timestamp"]
        for bid, se in biz_events.items():
            if "signup" in se and "first_cycle" in se:
                delta = (se["first_cycle"] - se["signup"]).total_seconds() / 3600
                activation_times.append(delta)
        avg_activation = (sum(activation_times) / len(activation_times)
                          if activation_times else 0.0)
        return OnboardingFunnel(
            total_signups=total,
            completed_profile=counts.get("profile_complete", 0),
            first_cycle=counts.get("first_cycle", 0),
            first_content=counts.get("first_content", 0),
            paid_conversion=counts.get("paid_conversion", 0),
            stage_rates=rates,
            avg_time_to_activation_hours=round(avg_activation, 1),
            drop_off_stages=drop_offs,
        )

    # -- A/B testing framework for conversion (496) --

    def create_experiment(
        self, name: str, description: str,
        variants: list[dict[str, Any]],
        metric: str = "conversion_rate",
        traffic_split: dict[str, float] | None = None,
    ) -> ABExperiment:
        exp_id = hashlib.sha256(
            f"{name}-{time.time()}".encode()
        ).hexdigest()[:12]
        if not traffic_split:
            n = len(variants)
            split = round(100 / n, 1)
            traffic_split = {v["name"]: split for v in variants}
        exp = ABExperiment(
            id=exp_id, name=name, description=description,
            variants=variants, traffic_split=traffic_split,
            metric=metric,
        )
        self._experiments[exp_id] = exp
        return exp

    def start_experiment(self, experiment_id: str) -> ABExperiment:
        exp = self._experiments.get(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        exp.status = "running"
        exp.start_at = datetime.now(timezone.utc)
        return exp

    def assign_variant(
        self, experiment_id: str, business_id: int,
    ) -> ABAssignment:
        exp = self._experiments.get(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        if exp.status != "running":
            raise ValueError("Experiment is not running")
        # check if already assigned
        for a in self._assignments:
            if a.experiment_id == experiment_id and a.business_id == business_id:
                return a
        # weighted random assignment based on traffic split
        variants = list(exp.traffic_split.keys())
        weights = [exp.traffic_split[v] for v in variants]
        # deterministic assignment based on business_id hash
        h = int(hashlib.md5(
            f"{experiment_id}-{business_id}".encode()
        ).hexdigest(), 16)
        cumulative = 0.0
        chosen = variants[0]
        target = (h % 1000) / 10  # 0-100
        for v, w in zip(variants, weights):
            cumulative += w
            if target < cumulative:
                chosen = v
                break
        assignment = ABAssignment(
            experiment_id=experiment_id,
            business_id=business_id, variant=chosen,
        )
        self._assignments.append(assignment)
        return assignment

    def record_conversion(
        self, experiment_id: str, business_id: int,
        metric_value: float = 1.0,
    ) -> ABAssignment | None:
        for a in self._assignments:
            if (a.experiment_id == experiment_id
                    and a.business_id == business_id):
                a.converted = True
                a.metric_value = metric_value
                return a
        return None

    def get_experiment_results(
        self, experiment_id: str,
    ) -> dict[str, Any]:
        exp = self._experiments.get(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        assignments = [a for a in self._assignments
                       if a.experiment_id == experiment_id]
        variant_stats: dict[str, dict[str, Any]] = {}
        for v in exp.traffic_split:
            v_assignments = [a for a in assignments if a.variant == v]
            v_conversions = [a for a in v_assignments if a.converted]
            total = len(v_assignments)
            converted = len(v_conversions)
            variant_stats[v] = {
                "participants": total,
                "conversions": converted,
                "conversion_rate": round(
                    converted / max(total, 1) * 100, 2),
                "avg_metric": round(
                    sum(a.metric_value for a in v_conversions)
                    / max(converted, 1), 3),
            }
        # determine winner
        best_variant = None
        best_rate = -1
        for v, stats in variant_stats.items():
            if stats["conversion_rate"] > best_rate:
                best_rate = stats["conversion_rate"]
                best_variant = v
        exp.results = variant_stats
        total_participants = sum(
            s["participants"] for s in variant_stats.values())
        # only declare winner with sufficient sample
        if total_participants >= 30 and best_variant:
            exp.winner = best_variant
        return {
            "experiment_id": experiment_id,
            "name": exp.name,
            "status": exp.status,
            "variant_stats": variant_stats,
            "winner": exp.winner,
            "total_participants": total_participants,
            "statistical_significance": total_participants >= 30,
        }

    def complete_experiment(self, experiment_id: str) -> ABExperiment:
        exp = self._experiments.get(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        exp.status = "completed"
        exp.end_at = datetime.now(timezone.utc)
        self.get_experiment_results(experiment_id)
        return exp

    # -- helpers --

    def _matches_criteria(
        self, insight: CustomerInsight, criteria: dict[str, Any],
    ) -> bool:
        for key, value in criteria.items():
            if key == "plan" and insight.plan != value:
                return False
            if key == "min_ltv" and insight.lifetime_value_cents < value:
                return False
            if key == "max_ltv" and insight.lifetime_value_cents > value:
                return False
            if key == "min_engagement" and insight.engagement_score < value:
                return False
            if key == "max_engagement" and insight.engagement_score > value:
                return False
            if key == "min_churn_risk" and insight.churn_risk < value:
                return False
            if key == "max_churn_risk" and insight.churn_risk > value:
                return False
            if key == "min_cycles" and insight.total_cycles < value:
                return False
        return True

    def _compute_stage_durations(self, journey: JourneyMap) -> None:
        stages_seen: dict[str, datetime] = {}
        for event in journey.events:
            if event.stage not in stages_seen:
                stages_seen[event.stage] = event.timestamp
        stage_order = [s.value for s in JourneyStage]
        ordered = sorted(
            stages_seen.items(),
            key=lambda x: stage_order.index(x[0])
            if x[0] in stage_order else 999,
        )
        for i in range(len(ordered) - 1):
            stage_name = ordered[i][0]
            duration = (ordered[i + 1][1] - ordered[i][1]).total_seconds() / 3600
            journey.stage_durations[stage_name] = round(duration, 2)
