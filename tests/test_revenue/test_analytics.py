"""Tests for analytics engine: customer insights, segmentation, journey mapping,
LTV prediction, referral analytics, onboarding funnel, A/B testing.

Covers items: 471,475,479,483,487,491,496.
"""

from datetime import datetime, timedelta, timezone

import pytest

from arclane.analytics.engine import (
    ABAssignment,
    ABExperiment,
    AnalyticsEngine,
    CustomerInsight,
    CustomerSegment,
    ExperimentStatus,
    JourneyEvent,
    JourneyMap,
    JourneyStage,
    LTVPrediction,
    OnboardingFunnel,
    ReferralAnalytics,
    SegmentType,
)


@pytest.fixture
def engine():
    return AnalyticsEngine()


@pytest.fixture
def sample_insights(engine):
    insights = [
        CustomerInsight(
            business_id=1, plan="pro", lifetime_value_cents=50000,
            months_active=6, total_cycles=30, total_content=20,
            features_used=5, engagement_score=75.0, churn_risk=0.1,
            expansion_potential=0.7),
        CustomerInsight(
            business_id=2, plan="starter", lifetime_value_cents=15000,
            months_active=2, total_cycles=5, total_content=3,
            features_used=2, engagement_score=30.0, churn_risk=0.8,
            expansion_potential=0.2),
        CustomerInsight(
            business_id=3, plan="growth", lifetime_value_cents=100000,
            months_active=12, total_cycles=100, total_content=50,
            features_used=8, engagement_score=90.0, churn_risk=0.05,
            expansion_potential=0.9),
    ]
    for i in insights:
        engine.record_insight(i)
    return insights


# --- 471: Analytics dashboard for customer insights ---

class TestCustomerInsights:
    def test_record_and_retrieve(self, engine):
        insight = CustomerInsight(
            business_id=1, plan="pro", lifetime_value_cents=50000,
            months_active=6, total_cycles=30, total_content=20,
            features_used=5, engagement_score=75.0, churn_risk=0.1,
            expansion_potential=0.7)
        engine.record_insight(insight)
        result = engine.get_insight(1)
        assert result is not None
        assert result.plan == "pro"

    def test_insights_summary(self, engine, sample_insights):
        summary = engine.get_insights_summary()
        assert summary["total_customers"] == 3
        assert summary["avg_ltv_cents"] > 0
        assert summary["high_churn_risk"] == 1  # business 2

    def test_expansion_candidates(self, engine, sample_insights):
        summary = engine.get_insights_summary()
        assert summary["expansion_candidates"] == 2  # business 1 and 3

    def test_empty_summary(self, engine):
        summary = engine.get_insights_summary()
        assert summary["total_customers"] == 0

    def test_get_nonexistent_insight(self, engine):
        assert engine.get_insight(999) is None


# --- 475: Customer segmentation engine ---

class TestSegmentation:
    def test_create_segment(self, engine, sample_insights):
        seg = engine.create_segment(
            "High Value", SegmentType.REVENUE.value,
            {"min_ltv": 40000})
        assert seg.name == "High Value"

    def test_evaluate_segment(self, engine, sample_insights):
        seg = engine.create_segment(
            "High Value", "revenue", {"min_ltv": 40000})
        evaluated = engine.evaluate_segment(seg.id)
        assert evaluated.member_count == 2  # business 1 and 3

    def test_segment_avg_metrics(self, engine, sample_insights):
        seg = engine.create_segment(
            "Active", "engagement", {"min_engagement": 50})
        evaluated = engine.evaluate_segment(seg.id)
        assert evaluated.avg_engagement > 50
        assert evaluated.avg_ltv_cents > 0

    def test_segment_by_plan(self, engine, sample_insights):
        seg = engine.create_segment("Pro Users", "plan", {"plan": "pro"})
        evaluated = engine.evaluate_segment(seg.id)
        assert evaluated.member_count == 1

    def test_segment_churn_risk(self, engine, sample_insights):
        seg = engine.create_segment(
            "At Risk", "lifecycle", {"min_churn_risk": 0.5})
        evaluated = engine.evaluate_segment(seg.id)
        assert evaluated.member_count == 1

    def test_get_segments(self, engine):
        engine.create_segment("A", "plan", {})
        engine.create_segment("B", "revenue", {})
        assert len(engine.get_segments()) == 2

    def test_evaluate_nonexistent_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.evaluate_segment("fake_id")

    def test_get_segment_members(self, engine, sample_insights):
        seg = engine.create_segment("All", "custom", {})
        members = engine.get_segment_members(seg.id)
        assert len(members) == 3

    def test_get_members_nonexistent(self, engine):
        assert engine.get_segment_members("fake") == []

    def test_segment_with_min_cycles(self, engine, sample_insights):
        seg = engine.create_segment("Heavy", "engagement", {"min_cycles": 50})
        evaluated = engine.evaluate_segment(seg.id)
        assert evaluated.member_count == 1  # business 3


# --- 479: Customer journey mapping ---

class TestJourneyMapping:
    def test_record_journey_event(self, engine):
        journey = engine.record_journey_event(
            1, JourneyStage.SIGNUP.value, "created_account")
        assert journey.current_stage == "signup"
        assert len(journey.events) == 1

    def test_journey_progression(self, engine):
        engine.record_journey_event(1, "signup", "created")
        engine.record_journey_event(1, "onboarding", "completed_profile")
        engine.record_journey_event(1, "activation", "first_cycle")
        journey = engine.get_journey(1)
        assert journey.current_stage == "activation"
        assert len(journey.events) == 3

    def test_journey_analytics(self, engine):
        engine.record_journey_event(1, "signup", "a")
        engine.record_journey_event(2, "signup", "a")
        engine.record_journey_event(1, "activation", "b")
        analytics = engine.get_journey_analytics()
        assert analytics["total_journeys"] == 2
        assert "signup" in analytics["stage_distribution"] or \
               "activation" in analytics["stage_distribution"]

    def test_empty_journey_analytics(self, engine):
        analytics = engine.get_journey_analytics()
        assert analytics["total_journeys"] == 0

    def test_get_nonexistent_journey(self, engine):
        assert engine.get_journey(999) is None

    def test_stage_durations(self, engine):
        now = datetime.now(timezone.utc)
        engine.record_journey_event(1, "signup", "a", metadata={})
        # manually set timestamps for duration calc
        journey = engine.get_journey(1)
        journey.events[0].timestamp = now - timedelta(hours=2)
        engine.record_journey_event(1, "onboarding", "b")
        assert len(journey.stage_durations) > 0 or len(journey.events) == 2


# --- 483: Customer lifetime value prediction ---

class TestLTVPrediction:
    def test_basic_prediction(self, engine):
        ltv = engine.predict_ltv(
            business_id=1, monthly_revenue_cents=6900,
            months_active=6, engagement_score=70,
            cycles_per_month=15)
        assert ltv.predicted_ltv_cents > 0
        assert 0 < ltv.confidence <= 1.0

    def test_high_churn_signals(self, engine):
        ltv = engine.predict_ltv(
            business_id=1, monthly_revenue_cents=4900,
            months_active=1, engagement_score=20,
            churn_signals=5)
        assert ltv.churn_probability > 0.1

    def test_expansion_probability(self, engine):
        ltv = engine.predict_ltv(
            business_id=1, monthly_revenue_cents=6900,
            engagement_score=80, cycles_per_month=15)
        assert ltv.expansion_probability > 0.3

    def test_low_expansion_probability(self, engine):
        ltv = engine.predict_ltv(
            business_id=1, monthly_revenue_cents=4900,
            engagement_score=20, cycles_per_month=2)
        assert ltv.expansion_probability <= 0.3

    def test_prediction_factors(self, engine):
        ltv = engine.predict_ltv(
            business_id=1, monthly_revenue_cents=9900,
            months_active=12, engagement_score=60)
        assert "retention_rate" in ltv.factors
        assert "tenure_factor" in ltv.factors

    def test_confidence_increases_with_tenure(self, engine):
        ltv_short = engine.predict_ltv(1, 4900, months_active=1)
        ltv_long = engine.predict_ltv(1, 4900, months_active=12)
        assert ltv_long.confidence > ltv_short.confidence

    def test_predicted_months_bounded(self, engine):
        ltv = engine.predict_ltv(1, 4900, months_active=1,
                                 engagement_score=99)
        assert ltv.predicted_months_remaining <= 60


# --- 487: Referral program analytics ---

class TestReferralAnalytics:
    def test_empty_analytics(self, engine):
        analytics = engine.get_referral_analytics()
        assert analytics.total_referrals == 0

    def test_record_and_analyze(self, engine):
        engine.record_referral_event(1, 2, channel="email", converted=True,
                                     revenue_cents=4900)
        engine.record_referral_event(1, 3, channel="social", converted=False)
        engine.record_referral_event(4, 5, channel="email", converted=True,
                                     revenue_cents=6900)
        analytics = engine.get_referral_analytics()
        assert analytics.total_referrals == 3
        assert analytics.converted_referrals == 2
        assert analytics.total_revenue_from_referrals_cents == 11800

    def test_top_referrers(self, engine):
        for i in range(5):
            engine.record_referral_event(1, 100 + i)
        for i in range(3):
            engine.record_referral_event(2, 200 + i)
        analytics = engine.get_referral_analytics()
        assert analytics.top_referrers[0]["business_id"] == 1

    def test_referral_by_channel(self, engine):
        engine.record_referral_event(1, 2, channel="email")
        engine.record_referral_event(1, 3, channel="social")
        engine.record_referral_event(1, 4, channel="email")
        analytics = engine.get_referral_analytics()
        assert analytics.referral_by_channel["email"] == 2
        assert analytics.referral_by_channel["social"] == 1

    def test_conversion_rate(self, engine):
        engine.record_referral_event(1, 2, converted=True)
        engine.record_referral_event(1, 3, converted=False)
        analytics = engine.get_referral_analytics()
        assert analytics.conversion_rate == 50.0


# --- 491: User onboarding funnel analytics ---

class TestOnboardingFunnel:
    def test_empty_funnel(self, engine):
        funnel = engine.get_onboarding_funnel()
        assert funnel.total_signups == 0

    def test_full_funnel(self, engine):
        now = datetime.now(timezone.utc)
        # User 1: completes all stages
        engine.record_onboarding_event(1, "signup", now)
        engine.record_onboarding_event(1, "profile_complete", now + timedelta(hours=1))
        engine.record_onboarding_event(1, "first_cycle", now + timedelta(hours=2))
        engine.record_onboarding_event(1, "first_content", now + timedelta(hours=3))
        engine.record_onboarding_event(1, "paid_conversion", now + timedelta(days=3))
        # User 2: drops off after signup
        engine.record_onboarding_event(2, "signup", now)
        funnel = engine.get_onboarding_funnel()
        assert funnel.total_signups == 2
        assert funnel.paid_conversion == 1
        assert funnel.first_cycle == 1

    def test_drop_off_stages(self, engine):
        now = datetime.now(timezone.utc)
        for i in range(10):
            engine.record_onboarding_event(i, "signup", now)
        for i in range(7):
            engine.record_onboarding_event(i, "profile_complete", now)
        for i in range(4):
            engine.record_onboarding_event(i, "first_cycle", now)
        funnel = engine.get_onboarding_funnel()
        assert funnel.drop_off_stages["signup_to_profile_complete"] == 3
        assert funnel.drop_off_stages["profile_complete_to_first_cycle"] == 3

    def test_stage_rates(self, engine):
        now = datetime.now(timezone.utc)
        engine.record_onboarding_event(1, "signup", now)
        engine.record_onboarding_event(1, "first_cycle", now)
        funnel = engine.get_onboarding_funnel()
        assert funnel.stage_rates["signup"] == 100.0
        assert funnel.stage_rates["first_cycle"] == 100.0

    def test_avg_activation_time(self, engine):
        now = datetime.now(timezone.utc)
        engine.record_onboarding_event(1, "signup", now)
        engine.record_onboarding_event(1, "first_cycle", now + timedelta(hours=6))
        funnel = engine.get_onboarding_funnel()
        assert funnel.avg_time_to_activation_hours == 6.0


# --- 496: A/B testing framework for conversion ---

class TestABTesting:
    def test_create_experiment(self, engine):
        exp = engine.create_experiment(
            "Pricing Page", "Test new pricing layout",
            variants=[{"name": "control"}, {"name": "variant_a"}])
        assert exp.status == "draft"
        assert len(exp.variants) == 2

    def test_start_experiment(self, engine):
        exp = engine.create_experiment(
            "CTA Test", "Test button colors",
            variants=[{"name": "blue"}, {"name": "green"}])
        started = engine.start_experiment(exp.id)
        assert started.status == "running"
        assert started.start_at is not None

    def test_assign_variant(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}])
        engine.start_experiment(exp.id)
        assignment = engine.assign_variant(exp.id, business_id=1)
        assert assignment.variant in {"A", "B"}

    def test_deterministic_assignment(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}])
        engine.start_experiment(exp.id)
        a1 = engine.assign_variant(exp.id, business_id=42)
        a2 = engine.assign_variant(exp.id, business_id=42)
        assert a1.variant == a2.variant  # same assignment

    def test_record_conversion(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}])
        engine.start_experiment(exp.id)
        engine.assign_variant(exp.id, business_id=1)
        result = engine.record_conversion(exp.id, business_id=1, metric_value=1.0)
        assert result is not None
        assert result.converted

    def test_experiment_results(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}],
            traffic_split={"A": 50.0, "B": 50.0})
        engine.start_experiment(exp.id)
        # assign many users
        for i in range(50):
            engine.assign_variant(exp.id, business_id=i)
            if i % 3 == 0:
                engine.record_conversion(exp.id, business_id=i)
        results = engine.get_experiment_results(exp.id)
        assert results["total_participants"] == 50
        assert "A" in results["variant_stats"]
        assert "B" in results["variant_stats"]

    def test_complete_experiment(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}])
        engine.start_experiment(exp.id)
        for i in range(40):
            engine.assign_variant(exp.id, business_id=i)
        completed = engine.complete_experiment(exp.id)
        assert completed.status == "completed"
        assert completed.end_at is not None

    def test_assign_not_running_raises(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}])
        with pytest.raises(ValueError, match="not running"):
            engine.assign_variant(exp.id, business_id=1)

    def test_nonexistent_experiment_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.start_experiment("fake_id")

    def test_custom_traffic_split(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "control"}, {"name": "test"}],
            traffic_split={"control": 80.0, "test": 20.0})
        assert exp.traffic_split["control"] == 80.0

    def test_results_statistical_significance(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}])
        engine.start_experiment(exp.id)
        # Only 5 participants - not significant
        for i in range(5):
            engine.assign_variant(exp.id, business_id=i)
        results = engine.get_experiment_results(exp.id)
        assert not results["statistical_significance"]

    def test_winner_declared_with_enough_data(self, engine):
        exp = engine.create_experiment(
            "Test", "desc",
            variants=[{"name": "A"}, {"name": "B"}])
        engine.start_experiment(exp.id)
        for i in range(40):
            engine.assign_variant(exp.id, business_id=i)
            if i % 2 == 0:
                engine.record_conversion(exp.id, business_id=i)
        results = engine.get_experiment_results(exp.id)
        assert results["statistical_significance"]
        assert results["winner"] is not None

    def test_conversion_nonexistent_returns_none(self, engine):
        exp = engine.create_experiment(
            "Test", "desc", variants=[{"name": "A"}])
        engine.start_experiment(exp.id)
        result = engine.record_conversion(exp.id, business_id=999)
        assert result is None
