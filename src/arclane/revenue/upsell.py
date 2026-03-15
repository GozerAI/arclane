"""Upsell, conversion, and retention engine.

Covers: 292,296,300,306,311,317,321,328,333,337,343,347,351,355,359,363,
        367,372,376,380,385,389,393.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from arclane.revenue.pricing import PLAN_CATALOG, FEATURE_COMPLEXITY


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PromptType(str, Enum):
    UPGRADE_AT_LIMIT = "upgrade_at_limit"
    CONTEXTUAL_CTA = "contextual_cta"
    MILESTONE_CELEBRATION = "milestone_celebration"
    FEATURE_SPOTLIGHT = "feature_spotlight"
    COMPETITOR_COMPARISON = "competitor_comparison"
    SUCCESS_STORY = "success_story"
    WINBACK = "winback"
    REENGAGEMENT = "reengagement"
    DECLINE_OUTREACH = "decline_outreach"
    REONBOARDING = "reonboarding"
    SATISFACTION_SURVEY = "satisfaction_survey"
    ADOPTION_NUDGE = "adoption_nudge"
    TRIAL_MILESTONE = "trial_milestone"
    TRIAL_EXPIRATION = "trial_expiration"
    TRIAL_CHECKIN = "trial_checkin"
    TRIAL_PROGRESS = "trial_progress"
    TRIAL_COMPARISON = "trial_comparison"


class EngagementLevel(str, Enum):
    HIGHLY_ACTIVE = "highly_active"
    ACTIVE = "active"
    MODERATE = "moderate"
    LOW = "low"
    INACTIVE = "inactive"
    CHURNED = "churned"


class DemoFeature(str, Enum):
    ADVANCED_ANALYTICS = "advanced_analytics"
    AB_TESTING = "ab_testing"
    CUSTOM_REPORTS = "custom_reports"
    WHITE_LABEL = "white_label"
    SEO_OPTIMIZATION = "seo_optimization"
    SOCIAL_SCHEDULING = "social_scheduling"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UpgradePrompt:
    business_id: int
    prompt_type: str
    current_plan: str
    suggested_plan: str | None
    message: str
    cta_text: str
    cta_url: str
    priority: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dismissed: bool = False
    converted: bool = False


@dataclass
class UserBehavior:
    business_id: int
    plan: str = "starter"
    credits_used: int = 0
    credits_total: int = 5
    cycles_run: int = 0
    features_used: set[str] = field(default_factory=set)
    last_active_at: datetime | None = None
    signup_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trial_end_at: datetime | None = None
    is_trial: bool = False
    churned_at: datetime | None = None
    monthly_revenue_cents: int = 0
    employee_count: int = 1
    logins_last_30d: int = 0
    pages_viewed_last_30d: int = 0
    support_tickets: int = 0
    nps_score: int | None = None
    referrals_made: int = 0
    content_created: int = 0
    satisfaction_responses: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EngagementScore:
    business_id: int
    score: float  # 0-100
    level: str
    factors: dict[str, float] = field(default_factory=dict)
    triggers: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DemoSession:
    business_id: int
    feature: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=24))
    actions_taken: int = 0
    max_actions: int = 10
    converted: bool = False


@dataclass
class TrialState:
    business_id: int
    plan: str = "pro"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ends_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=14))
    milestones_reached: list[str] = field(default_factory=list)
    checkin_responses: list[dict[str, Any]] = field(default_factory=list)
    progress_snapshot: dict[str, Any] = field(default_factory=dict)
    converted: bool = False
    card_on_file: bool = False


@dataclass
class CompetitorComparison:
    competitor: str
    our_features: list[str]
    their_features: list[str]
    our_price_cents: int
    their_price_cents: int
    value_score: float
    savings_pct: float


@dataclass
class SuccessStory:
    id: str
    business_name: str
    industry: str
    plan: str
    metric_improvement: str
    quote: str
    before_value: float
    after_value: float
    improvement_pct: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProductTourStep:
    step_id: str
    title: str
    description: str
    target_element: str
    action: str
    completed: bool = False


@dataclass
class ProductTour:
    business_id: int
    steps: list[ProductTourStep] = field(default_factory=list)
    current_step: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


@dataclass
class CommunityPost:
    id: str
    author_business_id: int
    title: str
    body: str
    category: str
    upvotes: int = 0
    replies: int = 0
    pinned: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FeatureAdoptionRecord:
    business_id: int
    feature: str
    first_used_at: datetime | None = None
    usage_count: int = 0
    nudge_sent: bool = False
    adopted: bool = False


# ---------------------------------------------------------------------------
# Upsell / Conversion Engine
# ---------------------------------------------------------------------------

class UpsellEngine:
    """Generates contextual upgrade prompts, manages trials, demos,
    engagement scoring, and retention flows."""

    def __init__(self) -> None:
        self._behaviors: dict[int, UserBehavior] = {}
        self._prompts: list[UpgradePrompt] = []
        self._demos: dict[tuple[int, str], DemoSession] = {}
        self._trials: dict[int, TrialState] = {}
        self._tours: dict[int, ProductTour] = {}
        self._community_posts: list[CommunityPost] = []
        self._success_stories: list[SuccessStory] = []
        self._adoption: dict[tuple[int, str], FeatureAdoptionRecord] = {}
        self._competitors: dict[str, CompetitorComparison] = {}

    # -- behavior tracking --

    def track_behavior(self, behavior: UserBehavior) -> UserBehavior:
        self._behaviors[behavior.business_id] = behavior
        return behavior

    def get_behavior(self, business_id: int) -> UserBehavior | None:
        return self._behaviors.get(business_id)

    # -- in-app upgrade prompts at usage limits (292) --

    def generate_limit_prompt(self, business_id: int) -> UpgradePrompt | None:
        beh = self._require_behavior(business_id)
        if beh.credits_total <= 0:
            return None
        usage_pct = (beh.credits_used / beh.credits_total) * 100
        if usage_pct < 80:
            return None
        plan_order = list(PLAN_CATALOG.keys())
        idx = plan_order.index(beh.plan) if beh.plan in plan_order else 0
        if idx >= len(plan_order) - 1:
            return None
        next_plan = plan_order[idx + 1]
        next_info = PLAN_CATALOG[next_plan]
        if usage_pct >= 100:
            msg = (f"You've used all {beh.credits_total} credits! "
                   f"Upgrade to {next_info['name']} for {next_info['credits']} credits/month.")
            priority = 10
        else:
            msg = (f"You've used {usage_pct:.0f}% of your credits. "
                   f"Upgrade to {next_info['name']} to avoid interruptions.")
            priority = 7
        prompt = UpgradePrompt(
            business_id=business_id,
            prompt_type=PromptType.UPGRADE_AT_LIMIT.value,
            current_plan=beh.plan, suggested_plan=next_plan,
            message=msg, cta_text=f"Upgrade to {next_info['name']}",
            cta_url="/billing/upgrade", priority=priority,
        )
        self._prompts.append(prompt)
        return prompt

    # -- contextual upgrade CTAs based on user behavior (296) --

    def generate_contextual_cta(self, business_id: int) -> list[UpgradePrompt]:
        beh = self._require_behavior(business_id)
        prompts: list[UpgradePrompt] = []
        plan_info = PLAN_CATALOG.get(beh.plan, PLAN_CATALOG["starter"])
        plan_features = plan_info.get("features", set())
        # find features user tried to use but doesn't have
        missing = beh.features_used - plan_features
        plan_order = list(PLAN_CATALOG.keys())
        for feat in missing:
            for pname in plan_order:
                p = PLAN_CATALOG[pname]
                if feat in p.get("features", set()):
                    prompt = UpgradePrompt(
                        business_id=business_id,
                        prompt_type=PromptType.CONTEXTUAL_CTA.value,
                        current_plan=beh.plan, suggested_plan=pname,
                        message=f"Unlock {feat.replace('_', ' ').title()} with {p['name']}",
                        cta_text=f"Get {p['name']}", cta_url="/billing/upgrade",
                        priority=6, metadata={"feature": feat},
                    )
                    prompts.append(prompt)
                    break
        self._prompts.extend(prompts)
        return prompts

    # -- usage milestone celebration with upgrade offer (300) --

    def check_milestones(self, business_id: int) -> UpgradePrompt | None:
        beh = self._require_behavior(business_id)
        milestones = [10, 25, 50, 100, 250, 500]
        for m in milestones:
            if beh.cycles_run == m:
                plan_order = list(PLAN_CATALOG.keys())
                idx = plan_order.index(beh.plan) if beh.plan in plan_order else 0
                next_plan = plan_order[min(idx + 1, len(plan_order) - 1)]
                prompt = UpgradePrompt(
                    business_id=business_id,
                    prompt_type=PromptType.MILESTONE_CELEBRATION.value,
                    current_plan=beh.plan, suggested_plan=next_plan,
                    message=f"Congratulations! You've completed {m} cycles! "
                            f"Unlock more with {PLAN_CATALOG[next_plan]['name']}.",
                    cta_text="Celebrate & Upgrade",
                    cta_url="/billing/upgrade",
                    priority=8, metadata={"milestone": m},
                )
                self._prompts.append(prompt)
                return prompt
        return None

    # -- demo mode for premium features (306) --

    def start_demo(
        self, business_id: int, feature: str, duration_hours: int = 24,
        max_actions: int = 10,
    ) -> DemoSession:
        if feature not in DemoFeature.__members__.values() and feature not in [e.value for e in DemoFeature]:
            # Allow any feature string for flexibility
            pass
        beh = self._require_behavior(business_id)
        plan_info = PLAN_CATALOG.get(beh.plan, PLAN_CATALOG["starter"])
        if feature in plan_info.get("features", set()):
            raise ValueError(f"Feature {feature} already included in {beh.plan}")
        session = DemoSession(
            business_id=business_id, feature=feature,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=duration_hours),
            max_actions=max_actions,
        )
        self._demos[(business_id, feature)] = session
        return session

    def use_demo_action(self, business_id: int, feature: str) -> DemoSession:
        key = (business_id, feature)
        session = self._demos.get(key)
        if not session:
            raise ValueError("No active demo session")
        now = datetime.now(timezone.utc)
        if now > session.expires_at:
            raise ValueError("Demo session expired")
        if session.actions_taken >= session.max_actions:
            raise ValueError("Demo action limit reached")
        session.actions_taken += 1
        return session

    def get_active_demos(self, business_id: int) -> list[DemoSession]:
        now = datetime.now(timezone.utc)
        return [
            s for (bid, _), s in self._demos.items()
            if bid == business_id and now <= s.expires_at
            and s.actions_taken < s.max_actions
        ]

    # -- power user identification for enterprise upsell (311) --

    def identify_power_users(
        self, min_cycles: int = 20, min_features: int = 4,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for bid, beh in self._behaviors.items():
            if beh.cycles_run >= min_cycles and len(beh.features_used) >= min_features:
                score = (
                    (beh.cycles_run / 100) * 40
                    + (len(beh.features_used) / 10) * 30
                    + (beh.content_created / 50) * 30
                )
                results.append({
                    "business_id": bid,
                    "plan": beh.plan,
                    "cycles_run": beh.cycles_run,
                    "features_used": len(beh.features_used),
                    "content_created": beh.content_created,
                    "power_score": min(round(score, 1), 100),
                    "recommended_plan": "enterprise"
                    if beh.plan != "enterprise" else beh.plan,
                })
        results.sort(key=lambda x: x["power_score"], reverse=True)
        return results

    # -- feature spotlight notifications for unused premium features (317) --

    def generate_feature_spotlights(
        self, business_id: int,
    ) -> list[UpgradePrompt]:
        beh = self._require_behavior(business_id)
        plan_info = PLAN_CATALOG.get(beh.plan, PLAN_CATALOG["starter"])
        available = plan_info.get("features", set())
        unused = available - beh.features_used
        prompts: list[UpgradePrompt] = []
        for feat in sorted(unused):
            prompt = UpgradePrompt(
                business_id=business_id,
                prompt_type=PromptType.FEATURE_SPOTLIGHT.value,
                current_plan=beh.plan, suggested_plan=None,
                message=f"Did you know your plan includes "
                        f"{feat.replace('_', ' ').title()}? Try it out!",
                cta_text=f"Try {feat.replace('_', ' ').title()}",
                cta_url=f"/features/{feat}",
                priority=4, metadata={"feature": feat},
            )
            prompts.append(prompt)
        self._prompts.extend(prompts)
        return prompts

    # -- comparison with competitors (321) --

    def register_competitor(self, comparison: CompetitorComparison) -> None:
        self._competitors[comparison.competitor] = comparison

    def get_competitor_comparison(
        self, plan: str,
    ) -> list[CompetitorComparison]:
        plan_info = PLAN_CATALOG.get(plan, PLAN_CATALOG["starter"])
        results: list[CompetitorComparison] = []
        for comp in self._competitors.values():
            comp_copy = CompetitorComparison(
                competitor=comp.competitor,
                our_features=sorted(plan_info.get("features", set())),
                their_features=comp.their_features,
                our_price_cents=plan_info["price_cents"],
                their_price_cents=comp.their_price_cents,
                value_score=len(plan_info.get("features", set())) / max(
                    len(comp.their_features), 1),
                savings_pct=round(
                    (1 - plan_info["price_cents"] / max(comp.their_price_cents, 1)) * 100, 1
                ) if comp.their_price_cents > 0 else 0,
            )
            results.append(comp_copy)
        return results

    # -- success story sharing (328) --

    def add_success_story(self, story: SuccessStory) -> SuccessStory:
        if not story.id:
            story.id = hashlib.sha256(
                f"{story.business_name}-{time.time()}".encode()
            ).hexdigest()[:12]
        self._success_stories.append(story)
        return story

    def get_relevant_stories(
        self, plan: str | None = None, industry: str | None = None,
        limit: int = 5,
    ) -> list[SuccessStory]:
        stories = self._success_stories[:]
        if plan:
            stories = [s for s in stories if s.plan == plan] or stories
        if industry:
            stories = [s for s in stories if s.industry == industry] or stories
        stories.sort(key=lambda s: s.improvement_pct, reverse=True)
        return stories[:limit]

    # -- win-back flow for churned customers (333) --

    def generate_winback(self, business_id: int) -> UpgradePrompt | None:
        beh = self._require_behavior(business_id)
        if not beh.churned_at:
            return None
        days_since = (datetime.now(timezone.utc) - beh.churned_at).days
        if days_since <= 0:
            days_since = 1
        if days_since <= 7:
            discount = 20
            msg = "We miss you! Come back with 20% off your first month."
        elif days_since <= 30:
            discount = 30
            msg = "It's been a while! Here's 30% off to welcome you back."
        elif days_since <= 90:
            discount = 40
            msg = "Special offer: 40% off any plan for returning customers."
        else:
            discount = 50
            msg = "We'd love to have you back -- 50% off for 3 months!"
        prompt = UpgradePrompt(
            business_id=business_id,
            prompt_type=PromptType.WINBACK.value,
            current_plan=beh.plan, suggested_plan=beh.plan,
            message=msg, cta_text=f"Reactivate ({discount}% off)",
            cta_url="/billing/reactivate",
            priority=9, metadata={"discount_pct": discount,
                                  "days_since_churn": days_since},
        )
        self._prompts.append(prompt)
        return prompt

    # -- engagement scoring with re-engagement triggers (337) --

    def compute_engagement_score(self, business_id: int) -> EngagementScore:
        beh = self._require_behavior(business_id)
        factors: dict[str, float] = {}
        # login frequency (0-25)
        login_score = min(25, beh.logins_last_30d * 0.83)
        factors["logins"] = round(login_score, 1)
        # feature diversity (0-25)
        feat_score = min(25, len(beh.features_used) * 3.5)
        factors["feature_diversity"] = round(feat_score, 1)
        # cycle activity (0-25)
        cycle_score = min(25, beh.cycles_run * 0.5)
        factors["cycle_activity"] = round(cycle_score, 1)
        # content creation (0-25)
        content_score = min(25, beh.content_created * 1.25)
        factors["content_creation"] = round(content_score, 1)
        total = sum(factors.values())
        triggers: list[str] = []
        if total < 20:
            level = EngagementLevel.INACTIVE.value
            triggers.append("send_reactivation_email")
            triggers.append("offer_onboarding_call")
        elif total < 40:
            level = EngagementLevel.LOW.value
            triggers.append("send_feature_tips")
            triggers.append("schedule_checkin")
        elif total < 60:
            level = EngagementLevel.MODERATE.value
            triggers.append("suggest_advanced_features")
        elif total < 80:
            level = EngagementLevel.ACTIVE.value
            triggers.append("request_testimonial")
        else:
            level = EngagementLevel.HIGHLY_ACTIVE.value
            triggers.append("offer_enterprise_demo")
            triggers.append("request_case_study")
        return EngagementScore(
            business_id=business_id, score=round(total, 1),
            level=level, factors=factors, triggers=triggers,
        )

    # -- usage decline detection (343) --

    def detect_usage_decline(
        self, business_id: int,
        current_period_cycles: int, previous_period_cycles: int,
    ) -> dict[str, Any] | None:
        if previous_period_cycles <= 0:
            return None
        decline_pct = (
            (previous_period_cycles - current_period_cycles)
            / previous_period_cycles * 100
        )
        if decline_pct < 20:
            return None
        if decline_pct >= 75:
            urgency = "critical"
            action = "immediate_outreach_call"
        elif decline_pct >= 50:
            urgency = "high"
            action = "send_personalized_email"
        else:
            urgency = "medium"
            action = "send_tips_and_resources"
        return {
            "business_id": business_id,
            "decline_pct": round(decline_pct, 1),
            "current_cycles": current_period_cycles,
            "previous_cycles": previous_period_cycles,
            "urgency": urgency,
            "recommended_action": action,
        }

    # -- personalized re-onboarding for inactive users (347) --

    def generate_reonboarding(self, business_id: int) -> dict[str, Any] | None:
        beh = self._require_behavior(business_id)
        if beh.last_active_at is None:
            days_inactive = 999
        else:
            days_inactive = (datetime.now(timezone.utc) - beh.last_active_at).days
        if days_inactive < 14:
            return None
        plan_info = PLAN_CATALOG.get(beh.plan, PLAN_CATALOG["starter"])
        unused_features = sorted(
            plan_info.get("features", set()) - beh.features_used)
        steps: list[dict[str, str]] = []
        if unused_features:
            steps.append({
                "title": "Explore New Features",
                "description": f"Try {unused_features[0].replace('_', ' ').title()}",
            })
        steps.append({
            "title": "Run a Quick Cycle",
            "description": "See what your AI executives have been working on",
        })
        steps.append({
            "title": "Review Your Results",
            "description": "Check the content and insights generated for your business",
        })
        return {
            "business_id": business_id,
            "days_inactive": days_inactive,
            "steps": steps,
            "unused_features": unused_features,
            "incentive": "Bonus 2 credits for completing re-onboarding"
            if days_inactive >= 30 else None,
        }

    # -- customer satisfaction surveys (351) --

    def create_satisfaction_survey(
        self, business_id: int,
    ) -> dict[str, Any]:
        return {
            "business_id": business_id,
            "questions": [
                {"id": "overall", "text": "How satisfied are you with Arclane?",
                 "type": "nps", "scale": [0, 10]},
                {"id": "value", "text": "Does Arclane provide good value for money?",
                 "type": "rating", "scale": [1, 5]},
                {"id": "recommend", "text": "How likely are you to recommend Arclane?",
                 "type": "nps", "scale": [0, 10]},
                {"id": "improvement", "text": "What could we improve?",
                 "type": "open_text"},
            ],
        }

    def process_survey_response(
        self, business_id: int, responses: dict[str, Any],
    ) -> dict[str, Any]:
        beh = self._require_behavior(business_id)
        beh.satisfaction_responses.append(responses)
        triggers: list[str] = []
        overall = responses.get("overall", 5)
        if isinstance(overall, (int, float)):
            beh.nps_score = int(overall)
            if overall <= 3:
                triggers.append("escalate_to_support")
                triggers.append("offer_concession")
            elif overall <= 6:
                triggers.append("schedule_feedback_call")
            elif overall >= 9:
                triggers.append("request_testimonial")
                triggers.append("suggest_referral_program")
        value = responses.get("value", 3)
        if isinstance(value, (int, float)) and value <= 2:
            triggers.append("offer_pricing_review")
        return {
            "business_id": business_id,
            "triggers": triggers,
            "nps_category": "promoter" if overall >= 9
            else "passive" if overall >= 7 else "detractor",
        }

    # -- feature adoption tracking with onboarding nudges (355) --

    def track_feature_adoption(
        self, business_id: int, feature: str,
    ) -> FeatureAdoptionRecord:
        key = (business_id, feature)
        record = self._adoption.get(key)
        if not record:
            record = FeatureAdoptionRecord(
                business_id=business_id, feature=feature,
                first_used_at=datetime.now(timezone.utc),
            )
            self._adoption[key] = record
        record.usage_count += 1
        if record.usage_count >= 5:
            record.adopted = True
        return record

    def get_adoption_nudges(self, business_id: int) -> list[dict[str, Any]]:
        beh = self._behaviors.get(business_id)
        if not beh:
            return []
        plan_info = PLAN_CATALOG.get(beh.plan, PLAN_CATALOG["starter"])
        available = plan_info.get("features", set())
        nudges: list[dict[str, Any]] = []
        for feat in sorted(available):
            key = (business_id, feat)
            record = self._adoption.get(key)
            if not record or (not record.adopted and not record.nudge_sent):
                nudges.append({
                    "feature": feat,
                    "title": f"Try {feat.replace('_', ' ').title()}",
                    "usage_count": record.usage_count if record else 0,
                    "message": f"You haven't fully explored {feat.replace('_', ' ')} yet. "
                               f"Give it a try!",
                })
                if record:
                    record.nudge_sent = True
        return nudges

    # -- community features for stickiness (363) --

    def create_community_post(
        self, author_business_id: int, title: str, body: str,
        category: str = "general",
    ) -> CommunityPost:
        self._require_behavior(author_business_id)
        post_id = hashlib.sha256(
            f"{author_business_id}-{title}-{time.time()}".encode()
        ).hexdigest()[:12]
        post = CommunityPost(
            id=post_id, author_business_id=author_business_id,
            title=title, body=body, category=category,
        )
        self._community_posts.append(post)
        return post

    def get_community_posts(
        self, category: str | None = None, limit: int = 20,
    ) -> list[CommunityPost]:
        posts = self._community_posts[:]
        if category:
            posts = [p for p in posts if p.category == category]
        posts.sort(key=lambda p: p.created_at, reverse=True)
        return posts[:limit]

    def upvote_post(self, post_id: str) -> CommunityPost | None:
        for post in self._community_posts:
            if post.id == post_id:
                post.upvotes += 1
                return post
        return None

    # -- interactive product tour for new signups (367) --

    def create_product_tour(self, business_id: int) -> ProductTour:
        steps = [
            ProductTourStep(
                step_id="welcome", title="Welcome to Arclane",
                description="Let's get you set up with your AI-powered business assistant.",
                target_element="#welcome-banner", action="click",
            ),
            ProductTourStep(
                step_id="dashboard", title="Your Dashboard",
                description="This is where you'll see all activity and insights.",
                target_element="#dashboard-main", action="view",
            ),
            ProductTourStep(
                step_id="run_cycle", title="Run Your First Cycle",
                description="Click here to let your AI executives work on your business.",
                target_element="#run-cycle-btn", action="click",
            ),
            ProductTourStep(
                step_id="content", title="Review Generated Content",
                description="See what your AI team has created for you.",
                target_element="#content-tab", action="click",
            ),
            ProductTourStep(
                step_id="billing", title="Manage Your Plan",
                description="View credits, upgrade, or purchase additional credits.",
                target_element="#billing-link", action="click",
            ),
        ]
        tour = ProductTour(business_id=business_id, steps=steps)
        self._tours[business_id] = tour
        return tour

    def advance_tour(self, business_id: int) -> ProductTour | None:
        tour = self._tours.get(business_id)
        if not tour or tour.completed_at:
            return tour
        if tour.current_step < len(tour.steps):
            tour.steps[tour.current_step].completed = True
            tour.current_step += 1
        if tour.current_step >= len(tour.steps):
            tour.completed_at = datetime.now(timezone.utc)
        return tour

    # -- trial milestone celebrations (372) --

    def check_trial_milestones(self, business_id: int) -> UpgradePrompt | None:
        trial = self._trials.get(business_id)
        if not trial:
            return None
        beh = self._behaviors.get(business_id)
        if not beh:
            return None
        milestone_defs = [
            ("first_cycle", beh.cycles_run >= 1, "First cycle complete!"),
            ("five_cycles", beh.cycles_run >= 5, "5 cycles and counting!"),
            ("first_content", beh.content_created >= 1, "Your first content piece!"),
            ("multi_feature", len(beh.features_used) >= 3, "Power user in the making!"),
        ]
        for m_id, condition, msg in milestone_defs:
            if condition and m_id not in trial.milestones_reached:
                trial.milestones_reached.append(m_id)
                prompt = UpgradePrompt(
                    business_id=business_id,
                    prompt_type=PromptType.TRIAL_MILESTONE.value,
                    current_plan=trial.plan, suggested_plan=trial.plan,
                    message=f"{msg} Keep going to unlock the full power of Arclane.",
                    cta_text="Continue Trial",
                    cta_url="/dashboard",
                    priority=5, metadata={"milestone": m_id},
                )
                self._prompts.append(prompt)
                return prompt
        return None

    # -- time-pressure conversion / trial expiration countdown (376) --

    def get_trial_countdown(self, business_id: int) -> dict[str, Any] | None:
        trial = self._trials.get(business_id)
        if not trial or trial.converted:
            return None
        now = datetime.now(timezone.utc)
        remaining = trial.ends_at - now
        days_left = remaining.total_seconds() / 86400
        if days_left <= 0:
            urgency = "expired"
            message = "Your trial has ended. Subscribe now to keep your data."
        elif days_left <= 1:
            urgency = "critical"
            message = "Less than 24 hours left! Don't lose your progress."
        elif days_left <= 3:
            urgency = "high"
            message = f"Only {int(days_left)} days left in your trial."
        elif days_left <= 7:
            urgency = "medium"
            message = f"{int(days_left)} days remaining in your trial."
        else:
            urgency = "low"
            message = f"{int(days_left)} days left to explore."
        return {
            "business_id": business_id,
            "days_remaining": round(max(days_left, 0), 1),
            "urgency": urgency,
            "message": message,
            "ends_at": trial.ends_at.isoformat(),
        }

    # -- trial satisfaction check-in (380) --

    def trial_checkin(
        self, business_id: int, satisfaction: int, feedback: str = "",
    ) -> dict[str, Any]:
        trial = self._trials.get(business_id)
        if not trial:
            raise ValueError("No active trial")
        response = {
            "satisfaction": satisfaction,
            "feedback": feedback,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        trial.checkin_responses.append(response)
        actions: list[str] = []
        if satisfaction <= 3:
            actions.append("schedule_support_call")
            actions.append("extend_trial_3_days")
        elif satisfaction <= 6:
            actions.append("send_tips_email")
        else:
            actions.append("suggest_conversion")
        return {"response_recorded": True, "actions": actions}

    # -- saved trial progress to incentivize conversion (385) --

    def save_trial_progress(
        self, business_id: int, progress: dict[str, Any],
    ) -> dict[str, Any]:
        trial = self._trials.get(business_id)
        if not trial:
            raise ValueError("No active trial")
        trial.progress_snapshot = {
            **progress,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "saved": True,
            "message": "Your progress is saved. Subscribe to keep it permanently!",
            "data_at_risk": list(progress.keys()),
        }

    def get_trial_progress(self, business_id: int) -> dict[str, Any]:
        trial = self._trials.get(business_id)
        if not trial:
            return {"has_progress": False}
        return {
            "has_progress": bool(trial.progress_snapshot),
            "snapshot": trial.progress_snapshot,
            "milestones": trial.milestones_reached,
        }

    # -- trial comparison: before vs after value (389) --

    def get_trial_comparison(self, business_id: int) -> dict[str, Any] | None:
        beh = self._behaviors.get(business_id)
        trial = self._trials.get(business_id)
        if not beh or not trial:
            return None
        return {
            "business_id": business_id,
            "before": {
                "content_created": 0,
                "cycles_run": 0,
                "features_explored": 0,
                "estimated_hours_manual": 40,
            },
            "after": {
                "content_created": beh.content_created,
                "cycles_run": beh.cycles_run,
                "features_explored": len(beh.features_used),
                "estimated_hours_saved": beh.cycles_run * 2,
            },
            "value_generated": {
                "hours_saved": beh.cycles_run * 2,
                "content_pieces": beh.content_created,
                "estimated_value_cents": beh.content_created * 5000
                + beh.cycles_run * 2 * 5000,
            },
        }

    # -- credit card-less trial with upgrade gate (393) --

    def start_cardless_trial(
        self, business_id: int, plan: str = "pro", days: int = 14,
    ) -> TrialState:
        trial = TrialState(
            business_id=business_id, plan=plan,
            ends_at=datetime.now(timezone.utc) + timedelta(days=days),
            card_on_file=False,
        )
        self._trials[business_id] = trial
        beh = self._behaviors.get(business_id)
        if beh:
            beh.is_trial = True
            beh.trial_end_at = trial.ends_at
        return trial

    def check_upgrade_gate(self, business_id: int, feature: str) -> dict[str, Any]:
        trial = self._trials.get(business_id)
        if not trial:
            return {"allowed": True, "reason": "not_in_trial"}
        if trial.converted:
            return {"allowed": True, "reason": "converted"}
        now = datetime.now(timezone.utc)
        if now > trial.ends_at:
            return {
                "allowed": False, "reason": "trial_expired",
                "message": "Your trial has ended. Subscribe to continue.",
                "cta_url": "/billing/subscribe",
            }
        # gate advanced features without card
        gated_features = {"white_label", "custom_domain", "sla_99_9",
                          "dedicated_support", "ab_testing"}
        if feature in gated_features and not trial.card_on_file:
            return {
                "allowed": False, "reason": "card_required",
                "message": f"Add a payment method to try {feature.replace('_', ' ')}.",
                "cta_url": "/billing/add-card",
            }
        return {"allowed": True, "reason": "trial_active"}

    # -- helpers --

    def _require_behavior(self, business_id: int) -> UserBehavior:
        beh = self._behaviors.get(business_id)
        if not beh:
            raise ValueError(f"No behavior tracked for business {business_id}")
        return beh

    def register_trial(self, trial: TrialState) -> TrialState:
        self._trials[trial.business_id] = trial
        return trial
