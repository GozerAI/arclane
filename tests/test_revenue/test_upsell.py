"""Tests for upsell, conversion, and retention engine.

Covers items: 292,296,300,306,311,317,321,328,333,337,343,347,351,355,
              363,367,372,376,380,385,389,393.
"""

from datetime import datetime, timedelta, timezone

import pytest

from arclane.revenue.upsell import (
    CommunityPost,
    CompetitorComparison,
    DemoSession,
    EngagementLevel,
    EngagementScore,
    FeatureAdoptionRecord,
    ProductTour,
    PromptType,
    SuccessStory,
    TrialState,
    UpgradePrompt,
    UpsellEngine,
    UserBehavior,
)


@pytest.fixture
def engine():
    return UpsellEngine()


@pytest.fixture
def starter_user(engine):
    beh = UserBehavior(
        business_id=1, plan="starter", working_days_used=4, working_days_total=5,
        cycles_run=8, features_used={"basic_analytics", "content_generation"},
        last_active_at=datetime.now(timezone.utc), content_created=5,
        logins_last_30d=15,
    )
    return engine.track_behavior(beh)


@pytest.fixture
def pro_user(engine):
    beh = UserBehavior(
        business_id=2, plan="pro", working_days_used=10, working_days_total=20,
        cycles_run=25, features_used={"basic_analytics", "content_generation",
                                      "social_scheduling", "seo_optimization",
                                      "advanced_analytics"},
        last_active_at=datetime.now(timezone.utc), content_created=30,
        logins_last_30d=25, employee_count=10,
    )
    return engine.track_behavior(beh)


# --- 292: In-app upgrade prompts at usage limits ---

class TestUpgradePrompts:
    def test_generate_prompt_at_80pct(self, engine, starter_user):
        prompt = engine.generate_limit_prompt(1)
        assert prompt is not None
        assert prompt.prompt_type == PromptType.UPGRADE_AT_LIMIT.value

    def test_no_prompt_under_80pct(self, engine):
        beh = UserBehavior(
            business_id=3, plan="pro", working_days_used=5, working_days_total=20,
            cycles_run=2, last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        prompt = engine.generate_limit_prompt(3)
        assert prompt is None

    def test_prompt_at_100pct_high_priority(self, engine):
        beh = UserBehavior(
            business_id=4, plan="starter", working_days_used=5, working_days_total=5,
            cycles_run=5, last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        prompt = engine.generate_limit_prompt(4)
        assert prompt is not None
        assert prompt.priority == 10

    def test_no_prompt_top_tier(self, engine):
        beh = UserBehavior(
            business_id=5, plan="enterprise", working_days_used=900,
            working_days_total=999, cycles_run=100,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        prompt = engine.generate_limit_prompt(5)
        assert prompt is None


# --- 296: Contextual upgrade CTAs based on user behavior ---

class TestContextualCTAs:
    def test_cta_for_missing_feature(self, engine):
        beh = UserBehavior(
            business_id=6, plan="starter",
            features_used={"basic_analytics", "ab_testing"},  # ab_testing not in starter
            working_days_used=3, working_days_total=5, cycles_run=5,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        ctas = engine.generate_contextual_cta(6)
        assert len(ctas) > 0
        assert any("ab_testing" in c.metadata.get("feature", "") for c in ctas)

    def test_no_cta_all_features_included(self, engine, starter_user):
        # starter features only
        starter_user.features_used = {"basic_analytics", "content_generation"}
        ctas = engine.generate_contextual_cta(1)
        assert len(ctas) == 0


# --- 300: Usage milestone celebration with upgrade offer ---

class TestMilestones:
    def test_milestone_at_10_cycles(self, engine):
        beh = UserBehavior(
            business_id=7, plan="starter", cycles_run=10,
            working_days_used=3, working_days_total=5,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        prompt = engine.check_milestones(7)
        assert prompt is not None
        assert prompt.prompt_type == PromptType.MILESTONE_CELEBRATION.value

    def test_no_milestone_at_12(self, engine):
        beh = UserBehavior(
            business_id=8, plan="starter", cycles_run=12,
            working_days_used=3, working_days_total=5,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        prompt = engine.check_milestones(8)
        assert prompt is None

    def test_milestone_at_100(self, engine):
        beh = UserBehavior(
            business_id=9, plan="pro", cycles_run=100,
            working_days_used=10, working_days_total=20,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        prompt = engine.check_milestones(9)
        assert prompt is not None
        assert prompt.metadata["milestone"] == 100


# --- 306: Demo mode for premium features ---

class TestDemoMode:
    def test_start_demo(self, engine, starter_user):
        session = engine.start_demo(1, "advanced_analytics")
        assert session.feature == "advanced_analytics"
        assert session.actions_taken == 0

    def test_demo_action_tracking(self, engine, starter_user):
        engine.start_demo(1, "ab_testing")
        session = engine.use_demo_action(1, "ab_testing")
        assert session.actions_taken == 1

    def test_demo_action_limit(self, engine, starter_user):
        engine.start_demo(1, "custom_reports", max_actions=2)
        engine.use_demo_action(1, "custom_reports")
        engine.use_demo_action(1, "custom_reports")
        with pytest.raises(ValueError, match="limit reached"):
            engine.use_demo_action(1, "custom_reports")

    def test_demo_already_included_raises(self, engine, starter_user):
        with pytest.raises(ValueError, match="already included"):
            engine.start_demo(1, "basic_analytics")

    def test_active_demos(self, engine, starter_user):
        engine.start_demo(1, "ab_testing")
        engine.start_demo(1, "white_label")
        demos = engine.get_active_demos(1)
        assert len(demos) == 2

    def test_no_session_raises(self, engine, starter_user):
        with pytest.raises(ValueError, match="No active demo"):
            engine.use_demo_action(1, "nonexistent")


# --- 311: Power user identification for enterprise upsell ---

class TestPowerUsers:
    def test_identify_power_user(self, engine, pro_user):
        results = engine.identify_power_users(min_cycles=20, min_features=4)
        assert len(results) == 1
        assert results[0]["business_id"] == 2
        assert results[0]["recommended_plan"] == "enterprise"

    def test_no_power_users(self, engine, starter_user):
        results = engine.identify_power_users(min_cycles=100, min_features=10)
        assert len(results) == 0

    def test_power_score_bounded(self, engine, pro_user):
        results = engine.identify_power_users()
        for r in results:
            assert 0 <= r["power_score"] <= 100


# --- 317: Feature spotlight notifications for unused premium features ---

class TestFeatureSpotlights:
    def test_spotlight_unused_features(self, engine, starter_user):
        spotlights = engine.generate_feature_spotlights(1)
        assert len(spotlights) > 0
        # starter has single_user feature unused
        features_highlighted = {s.metadata["feature"] for s in spotlights}
        assert "single_user" in features_highlighted

    def test_no_spotlights_all_used(self, engine):
        from arclane.revenue.pricing import PLAN_CATALOG
        all_features = PLAN_CATALOG["starter"]["features"]
        beh = UserBehavior(
            business_id=10, plan="starter", features_used=all_features.copy(),
            working_days_used=3, working_days_total=5, cycles_run=5,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        spotlights = engine.generate_feature_spotlights(10)
        assert len(spotlights) == 0


# --- 321: Comparison with competitors ---

class TestCompetitorComparison:
    def test_register_and_compare(self, engine):
        comp = CompetitorComparison(
            competitor="CompetitorX",
            our_features=[], their_features=["content", "seo"],
            our_price_cents=0, their_price_cents=14900,
            value_score=0, savings_pct=0,
        )
        engine.register_competitor(comp)
        results = engine.get_competitor_comparison("pro")
        assert len(results) == 1
        assert results[0].savings_pct > 0  # we're cheaper

    def test_no_competitors(self, engine):
        results = engine.get_competitor_comparison("starter")
        assert len(results) == 0

    def test_value_score_calculated(self, engine):
        comp = CompetitorComparison(
            competitor="Rival", our_features=[], their_features=["a", "b"],
            our_price_cents=0, their_price_cents=9900,
            value_score=0, savings_pct=0,
        )
        engine.register_competitor(comp)
        results = engine.get_competitor_comparison("growth")
        assert results[0].value_score > 0


# --- 328: Success story sharing ---

class TestSuccessStories:
    def test_add_and_retrieve(self, engine):
        story = SuccessStory(
            id="s1", business_name="AcmeCorp", industry="saas",
            plan="pro", metric_improvement="50% more leads",
            quote="Arclane changed everything!",
            before_value=100, after_value=150, improvement_pct=50.0,
        )
        engine.add_success_story(story)
        stories = engine.get_relevant_stories(plan="pro")
        assert len(stories) == 1
        assert stories[0].business_name == "AcmeCorp"

    def test_filter_by_industry(self, engine):
        engine.add_success_story(SuccessStory(
            id="s2", business_name="B", industry="ecommerce",
            plan="growth", metric_improvement="", quote="",
            before_value=0, after_value=0, improvement_pct=30.0))
        engine.add_success_story(SuccessStory(
            id="s3", business_name="C", industry="saas",
            plan="pro", metric_improvement="", quote="",
            before_value=0, after_value=0, improvement_pct=40.0))
        stories = engine.get_relevant_stories(industry="saas")
        assert len(stories) == 1

    def test_stories_sorted_by_improvement(self, engine):
        for i, pct in enumerate([10, 50, 30]):
            engine.add_success_story(SuccessStory(
                id=f"s{i}", business_name=f"B{i}", industry="tech",
                plan="pro", metric_improvement="", quote="",
                before_value=0, after_value=0, improvement_pct=pct))
        stories = engine.get_relevant_stories()
        assert stories[0].improvement_pct >= stories[-1].improvement_pct


# --- 333: Win-back flow for churned customers ---

class TestWinback:
    def test_winback_recent_churn(self, engine):
        beh = UserBehavior(
            business_id=20, plan="pro",
            churned_at=datetime.now(timezone.utc) - timedelta(days=5),
            working_days_used=0, working_days_total=20, cycles_run=10,
            last_active_at=datetime.now(timezone.utc) - timedelta(days=5))
        engine.track_behavior(beh)
        prompt = engine.generate_winback(20)
        assert prompt is not None
        assert prompt.metadata["discount_pct"] == 20

    def test_winback_old_churn(self, engine):
        beh = UserBehavior(
            business_id=21, plan="starter",
            churned_at=datetime.now(timezone.utc) - timedelta(days=100),
            working_days_used=0, working_days_total=5, cycles_run=2,
            last_active_at=datetime.now(timezone.utc) - timedelta(days=100))
        engine.track_behavior(beh)
        prompt = engine.generate_winback(21)
        assert prompt is not None
        assert prompt.metadata["discount_pct"] == 50

    def test_no_winback_active_user(self, engine, starter_user):
        prompt = engine.generate_winback(1)
        assert prompt is None


# --- 337: Engagement scoring with re-engagement triggers ---

class TestEngagementScoring:
    def test_high_engagement(self, engine, pro_user):
        score = engine.compute_engagement_score(2)
        assert score.score > 50
        assert score.level in {e.value for e in EngagementLevel}

    def test_low_engagement(self, engine):
        beh = UserBehavior(
            business_id=30, plan="starter", logins_last_30d=1,
            features_used=set(), cycles_run=0, content_created=0,
            working_days_used=0, working_days_total=5,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        score = engine.compute_engagement_score(30)
        assert score.level == EngagementLevel.INACTIVE.value
        assert "send_reactivation_email" in score.triggers

    def test_engagement_factors(self, engine, starter_user):
        score = engine.compute_engagement_score(1)
        assert "logins" in score.factors
        assert "feature_diversity" in score.factors
        assert "cycle_activity" in score.factors

    def test_highly_active_triggers(self, engine):
        beh = UserBehavior(
            business_id=31, plan="growth", logins_last_30d=30,
            features_used={"a", "b", "c", "d", "e", "f", "g", "h"},
            cycles_run=60, content_created=25,
            working_days_used=40, working_days_total=60,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        score = engine.compute_engagement_score(31)
        assert score.level == EngagementLevel.HIGHLY_ACTIVE.value
        assert "offer_enterprise_demo" in score.triggers


# --- 343: Usage decline detection with proactive outreach ---

class TestUsageDecline:
    def test_detect_significant_decline(self, engine, starter_user):
        result = engine.detect_usage_decline(1, current_period_cycles=3,
                                             previous_period_cycles=15)
        assert result is not None
        assert result["urgency"] == "critical"

    def test_no_decline(self, engine, starter_user):
        result = engine.detect_usage_decline(1, current_period_cycles=15,
                                             previous_period_cycles=15)
        assert result is None

    def test_moderate_decline(self, engine, starter_user):
        result = engine.detect_usage_decline(1, current_period_cycles=7,
                                             previous_period_cycles=10)
        assert result is not None
        assert result["urgency"] == "medium"

    def test_no_previous_usage(self, engine, starter_user):
        result = engine.detect_usage_decline(1, current_period_cycles=0,
                                             previous_period_cycles=0)
        assert result is None


# --- 347: Personalized re-onboarding for inactive users ---

class TestReonboarding:
    def test_reonboarding_for_inactive(self, engine):
        beh = UserBehavior(
            business_id=40, plan="pro", features_used={"basic_analytics"},
            working_days_used=5, working_days_total=20, cycles_run=3,
            last_active_at=datetime.now(timezone.utc) - timedelta(days=30))
        engine.track_behavior(beh)
        result = engine.generate_reonboarding(40)
        assert result is not None
        assert len(result["steps"]) > 0
        assert result["incentive"] is not None  # 30 days inactive

    def test_no_reonboarding_active_user(self, engine, starter_user):
        result = engine.generate_reonboarding(1)
        assert result is None

    def test_reonboarding_lists_unused_features(self, engine):
        beh = UserBehavior(
            business_id=41, plan="pro", features_used=set(),
            working_days_used=0, working_days_total=20, cycles_run=0,
            last_active_at=datetime.now(timezone.utc) - timedelta(days=20))
        engine.track_behavior(beh)
        result = engine.generate_reonboarding(41)
        assert len(result["unused_features"]) > 0


# --- 351: Customer satisfaction surveys with action triggers ---

class TestSatisfactionSurveys:
    def test_create_survey(self, engine, starter_user):
        survey = engine.create_satisfaction_survey(1)
        assert len(survey["questions"]) == 4

    def test_process_good_response(self, engine, starter_user):
        result = engine.process_survey_response(1, {"overall": 9, "value": 5})
        assert result["nps_category"] == "promoter"
        assert "request_testimonial" in result["triggers"]

    def test_process_bad_response(self, engine, starter_user):
        result = engine.process_survey_response(1, {"overall": 2, "value": 1})
        assert result["nps_category"] == "detractor"
        assert "escalate_to_support" in result["triggers"]
        assert "offer_pricing_review" in result["triggers"]

    def test_survey_stored(self, engine, starter_user):
        engine.process_survey_response(1, {"overall": 7})
        assert len(starter_user.satisfaction_responses) == 1


# --- 355: Feature adoption tracking with onboarding nudges ---

class TestFeatureAdoption:
    def test_track_adoption(self, engine, starter_user):
        record = engine.track_feature_adoption(1, "content_generation")
        assert record.usage_count == 1
        assert not record.adopted

    def test_adopted_after_5_uses(self, engine, starter_user):
        for _ in range(5):
            record = engine.track_feature_adoption(1, "content_generation")
        assert record.adopted

    def test_nudges_for_unadopted(self, engine, starter_user):
        nudges = engine.get_adoption_nudges(1)
        assert len(nudges) > 0  # has unused features

    def test_nudge_sent_flag(self, engine, starter_user):
        nudges = engine.get_adoption_nudges(1)
        # second call should have fewer nudges (nudge_sent=True)
        nudges2 = engine.get_adoption_nudges(1)
        # features without any record get new nudges created
        assert len(nudges) >= len(nudges2)


# --- 363: Community features for stickiness ---

class TestCommunityFeatures:
    def test_create_post(self, engine, starter_user):
        post = engine.create_community_post(1, "My first post", "Hello!", "general")
        assert post.title == "My first post"
        assert post.upvotes == 0

    def test_get_posts_by_category(self, engine, starter_user):
        engine.create_community_post(1, "A", "a", "tips")
        engine.create_community_post(1, "B", "b", "general")
        posts = engine.get_community_posts(category="tips")
        assert len(posts) == 1

    def test_upvote_post(self, engine, starter_user):
        post = engine.create_community_post(1, "X", "y", "general")
        result = engine.upvote_post(post.id)
        assert result.upvotes == 1

    def test_upvote_nonexistent(self, engine):
        assert engine.upvote_post("fake_id") is None


# --- 367: Interactive product tour for new signups ---

class TestProductTour:
    def test_create_tour(self, engine, starter_user):
        tour = engine.create_product_tour(1)
        assert len(tour.steps) == 5
        assert tour.current_step == 0

    def test_advance_tour(self, engine, starter_user):
        engine.create_product_tour(1)
        tour = engine.advance_tour(1)
        assert tour.current_step == 1
        assert tour.steps[0].completed

    def test_complete_tour(self, engine, starter_user):
        engine.create_product_tour(1)
        for _ in range(5):
            tour = engine.advance_tour(1)
        assert tour.completed_at is not None

    def test_advance_completed_tour(self, engine, starter_user):
        engine.create_product_tour(1)
        for _ in range(5):
            engine.advance_tour(1)
        tour = engine.advance_tour(1)
        assert tour.completed_at is not None  # stays completed


# --- 372: Trial milestone celebrations ---

class TestTrialMilestones:
    def test_first_cycle_milestone(self, engine):
        beh = UserBehavior(
            business_id=50, plan="pro", cycles_run=1,
            working_days_used=1, working_days_total=20,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        trial = TrialState(business_id=50, plan="pro")
        engine.register_trial(trial)
        prompt = engine.check_trial_milestones(50)
        assert prompt is not None
        assert prompt.metadata["milestone"] == "first_cycle"

    def test_no_duplicate_milestone(self, engine):
        beh = UserBehavior(
            business_id=51, plan="pro", cycles_run=1,
            working_days_used=1, working_days_total=20,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        trial = TrialState(business_id=51, plan="pro")
        engine.register_trial(trial)
        engine.check_trial_milestones(51)
        prompt = engine.check_trial_milestones(51)
        assert prompt is None  # already reached

    def test_no_trial_no_milestone(self, engine, starter_user):
        prompt = engine.check_trial_milestones(1)
        assert prompt is None


# --- 376: Time-pressure conversion (trial expiration countdown) ---

class TestTrialCountdown:
    def test_countdown_active(self, engine, starter_user):
        trial = TrialState(
            business_id=1, plan="pro",
            ends_at=datetime.now(timezone.utc) + timedelta(days=10))
        engine.register_trial(trial)
        countdown = engine.get_trial_countdown(1)
        assert countdown is not None
        assert countdown["urgency"] == "low"

    def test_countdown_critical(self, engine, starter_user):
        trial = TrialState(
            business_id=1, plan="pro",
            ends_at=datetime.now(timezone.utc) + timedelta(hours=12))
        engine.register_trial(trial)
        countdown = engine.get_trial_countdown(1)
        assert countdown["urgency"] == "critical"

    def test_countdown_expired(self, engine, starter_user):
        trial = TrialState(
            business_id=1, plan="pro",
            ends_at=datetime.now(timezone.utc) - timedelta(days=1))
        engine.register_trial(trial)
        countdown = engine.get_trial_countdown(1)
        assert countdown["urgency"] == "expired"

    def test_no_countdown_converted(self, engine, starter_user):
        trial = TrialState(business_id=1, plan="pro", converted=True)
        engine.register_trial(trial)
        assert engine.get_trial_countdown(1) is None


# --- 380: Trial satisfaction check-in ---

class TestTrialCheckin:
    def test_good_checkin(self, engine, starter_user):
        trial = TrialState(business_id=1, plan="pro")
        engine.register_trial(trial)
        result = engine.trial_checkin(1, satisfaction=8, feedback="Great!")
        assert "suggest_conversion" in result["actions"]

    def test_bad_checkin(self, engine, starter_user):
        trial = TrialState(business_id=1, plan="pro")
        engine.register_trial(trial)
        result = engine.trial_checkin(1, satisfaction=2, feedback="Confused")
        assert "schedule_support_call" in result["actions"]

    def test_no_trial_raises(self, engine, starter_user):
        with pytest.raises(ValueError, match="No active trial"):
            engine.trial_checkin(1, satisfaction=5)


# --- 385: Saved trial progress to incentivize conversion ---

class TestTrialProgress:
    def test_save_progress(self, engine, starter_user):
        trial = TrialState(business_id=1, plan="pro")
        engine.register_trial(trial)
        result = engine.save_trial_progress(1, {"content_count": 5, "cycles": 3})
        assert result["saved"]
        assert "content_count" in result["data_at_risk"]

    def test_get_progress(self, engine, starter_user):
        trial = TrialState(business_id=1, plan="pro")
        engine.register_trial(trial)
        engine.save_trial_progress(1, {"data": "important"})
        progress = engine.get_trial_progress(1)
        assert progress["has_progress"]

    def test_no_trial_progress(self, engine, starter_user):
        progress = engine.get_trial_progress(1)
        assert not progress["has_progress"]


# --- 389: Trial comparison (before vs after value) ---

class TestTrialComparison:
    def test_comparison_with_activity(self, engine, starter_user):
        trial = TrialState(business_id=1, plan="pro")
        engine.register_trial(trial)
        comparison = engine.get_trial_comparison(1)
        assert comparison is not None
        assert comparison["after"]["cycles_run"] > 0
        assert comparison["value_generated"]["hours_saved"] > 0

    def test_no_trial_no_comparison(self, engine):
        beh = UserBehavior(
            business_id=60, plan="starter",
            working_days_used=0, working_days_total=5, cycles_run=0,
            last_active_at=datetime.now(timezone.utc))
        engine.track_behavior(beh)
        assert engine.get_trial_comparison(60) is None


# --- 393: Credit card-less trial with upgrade gate ---

class TestCardlessTrial:
    def test_start_cardless_trial(self, engine, starter_user):
        trial = engine.start_cardless_trial(1, plan="pro", days=14)
        assert not trial.card_on_file
        assert trial.plan == "pro"

    def test_upgrade_gate_allowed(self, engine, starter_user):
        engine.start_cardless_trial(1)
        result = engine.check_upgrade_gate(1, "basic_analytics")
        assert result["allowed"]

    def test_upgrade_gate_card_required(self, engine, starter_user):
        engine.start_cardless_trial(1)
        result = engine.check_upgrade_gate(1, "ab_testing")
        assert not result["allowed"]
        assert result["reason"] == "card_required"

    def test_upgrade_gate_expired(self, engine, starter_user):
        trial = engine.start_cardless_trial(1, days=0)
        trial.ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        result = engine.check_upgrade_gate(1, "basic_analytics")
        assert not result["allowed"]
        assert result["reason"] == "trial_expired"

    def test_upgrade_gate_converted(self, engine, starter_user):
        trial = engine.start_cardless_trial(1)
        trial.converted = True
        result = engine.check_upgrade_gate(1, "ab_testing")
        assert result["allowed"]

    def test_no_trial_allowed(self, engine, starter_user):
        result = engine.check_upgrade_gate(1, "anything")
        assert result["allowed"]
