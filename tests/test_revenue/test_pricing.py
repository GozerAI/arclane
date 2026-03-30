"""Tests for working-day-based metered billing, pay-per-cycle, usage caps,
dynamic pricing, working day rollover, gifting, business-size pricing,
ROI calculator, discounts, marketplace, auto-upgrade, referral bonuses,
variable pricing, working day expiration, spending caps, prepaid packages,
usage reporting, pricing adjustment.

Covers items: 253,256,259,262,265,268,271,274,277,280,283,286,289,
              359,444,447,451,460,463.
"""

from datetime import datetime, timedelta, timezone

import pytest

from arclane.revenue.pricing import (
    BUSINESS_SIZE_TIERS,
    DEMAND_BANDS,
    DISCOUNT_PROGRAMS,
    FEATURE_COMPLEXITY,
    PLAN_CATALOG,
    PREPAID_PACKAGES,
    AutoUpgradeSuggestion,
    BillingMode,
    WorkingDayAccount,
    CreditExpiration,
    CreditTransaction,
    DiscountProgram,
    MarketplaceListing,
    MeteredBillingEngine,
    PlanTier,
    ROIEstimate,
    UsageWarning,
)


@pytest.fixture
def engine():
    return MeteredBillingEngine()


@pytest.fixture
def starter_account(engine):
    acct = WorkingDayAccount(business_id=1, plan="starter", working_days_remaining=5)
    return engine.register_account(acct)


@pytest.fixture
def pro_account(engine):
    acct = WorkingDayAccount(business_id=2, plan="pro", working_days_remaining=20)
    return engine.register_account(acct)


# --- 253: Credit-based metered billing ---

class TestMeteredBilling:
    def test_consume_working_day_basic(self, engine, starter_account):
        txn = engine.consume_working_day(1)
        assert txn.delta == -1
        assert txn.balance_after == 4
        assert starter_account.working_days_remaining == 4

    def test_consume_multiple_working_days(self, engine, starter_account):
        txn = engine.consume_working_day(1, amount=3)
        assert txn.delta == -3
        assert starter_account.working_days_remaining == 2

    def test_consume_insufficient_working_days(self, engine, starter_account):
        with pytest.raises(ValueError, match="Insufficient working days"):
            engine.consume_working_day(1, amount=10)

    def test_metered_billing_mode_tracks_cost(self, engine):
        acct = WorkingDayAccount(
            business_id=10, plan="pro", working_days_remaining=20,
            billing_mode=BillingMode.METERED.value)
        engine.register_account(acct)
        engine.consume_working_day(10, amount=2)
        assert acct.metered_usage_cents > 0

    def test_consume_with_feature(self, engine, starter_account):
        txn = engine.consume_working_day(1, feature="content_generation")
        assert txn.feature == "content_generation"

    def test_consume_updates_total_spent(self, engine, starter_account):
        engine.consume_working_day(1, amount=2)
        assert starter_account.total_spent_cents > 0

    def test_no_account_raises(self, engine):
        with pytest.raises(ValueError, match="No account"):
            engine.consume_working_day(999)


# --- 256: Pay-per-cycle pricing ---

class TestPayPerCycle:
    def test_pay_per_cycle_basic(self, engine, starter_account):
        txn = engine.pay_per_cycle(1)
        assert txn.reason == "pay_per_cycle"
        assert txn.metadata["cost_cents"] > 0

    def test_pay_per_cycle_respects_plan_rate(self, engine, pro_account):
        txn = engine.pay_per_cycle(2)
        # Pro cycle price is 900 base
        assert txn.metadata["cost_cents"] > 0

    def test_pay_per_cycle_with_demand_multiplier(self, engine, starter_account):
        engine.set_system_demand(600)  # should be 1.25x
        txn = engine.pay_per_cycle(1)
        base = PLAN_CATALOG["starter"]["cycle_price_cents"]
        assert txn.metadata["cost_cents"] >= base

    def test_pay_per_cycle_spending_cap_enforced(self, engine, starter_account):
        starter_account.spending_cap_cents = 100
        with pytest.raises(ValueError, match="Spending cap"):
            engine.pay_per_cycle(1)

    def test_pay_per_cycle_demand_in_metadata(self, engine, starter_account):
        engine.set_system_demand(200)
        txn = engine.pay_per_cycle(1)
        assert "demand_multiplier" in txn.metadata


# --- 259: Usage caps with soft limit warnings ---

class TestUsageCaps:
    def test_no_warnings_under_limit(self, engine, starter_account):
        warnings = engine.check_usage_warnings(1)
        assert len(warnings) == 0

    def test_soft_limit_warning(self, engine):
        acct = WorkingDayAccount(business_id=3, plan="starter", working_days_remaining=1)
        engine.register_account(acct)
        warnings = engine.check_usage_warnings(3)
        assert any(w.warning_type == "soft_limit" for w in warnings)

    def test_hard_limit_warning(self, engine):
        acct = WorkingDayAccount(business_id=4, plan="starter", working_days_remaining=0)
        engine.register_account(acct)
        warnings = engine.check_usage_warnings(4)
        assert any(w.warning_type == "hard_limit" for w in warnings)

    def test_custom_soft_limit(self, engine):
        acct = WorkingDayAccount(
            business_id=5, plan="starter", working_days_remaining=2,
            soft_limit_pct=50.0)
        engine.register_account(acct)
        warnings = engine.check_usage_warnings(5)
        assert any(w.warning_type == "soft_limit" for w in warnings)

    def test_hard_cap_prevents_consumption(self, engine):
        acct = WorkingDayAccount(
            business_id=6, plan="starter", working_days_remaining=2, hard_cap=2)
        engine.register_account(acct)
        engine.consume_working_day(6, amount=1)
        engine.consume_working_day(6, amount=1)
        with pytest.raises(ValueError, match="Hard usage cap"):
            engine.consume_working_day(6, amount=1)


# --- 262: Dynamic working day pricing based on demand ---

class TestDynamicPricing:
    def test_base_price_low_demand(self, engine):
        engine.set_system_demand(50)
        price = engine.get_dynamic_price_cents("starter")
        assert price == PLAN_CATALOG["starter"]["cycle_price_cents"]

    def test_price_increases_with_demand(self, engine):
        engine.set_system_demand(0)
        low = engine.get_dynamic_price_cents("starter")
        engine.set_system_demand(600)
        high = engine.get_dynamic_price_cents("starter")
        assert high > low

    def test_highest_demand_band(self, engine):
        engine.set_system_demand(2000)
        price = engine.get_dynamic_price_cents("starter")
        base = PLAN_CATALOG["starter"]["cycle_price_cents"]
        assert price == round(base * 1.5)

    def test_negative_demand_clamped(self, engine):
        engine.set_system_demand(-10)
        price = engine.get_dynamic_price_cents("starter")
        assert price == PLAN_CATALOG["starter"]["cycle_price_cents"]


# --- 265: Credit rollover for annual plans ---

class TestCreditRollover:
    def test_rollover_annual_pro(self, engine):
        acct = WorkingDayAccount(
            business_id=7, plan="pro", working_days_remaining=10,
            annual_billing=True)
        engine.register_account(acct)
        rollover = engine.process_rollover(7)
        assert rollover > 0
        assert acct.rollover_working_days == rollover

    def test_no_rollover_monthly(self, engine, starter_account):
        rollover = engine.process_rollover(1)
        assert rollover == 0

    def test_no_rollover_starter_annual(self, engine):
        acct = WorkingDayAccount(
            business_id=8, plan="starter", working_days_remaining=3,
            annual_billing=True)
        engine.register_account(acct)
        rollover = engine.process_rollover(8)
        assert rollover == 0  # starter has 0% rollover

    def test_rollover_capped_at_plan_pct(self, engine):
        acct = WorkingDayAccount(
            business_id=9, plan="growth", working_days_remaining=50,
            annual_billing=True)
        engine.register_account(acct)
        rollover = engine.process_rollover(9)
        max_rollover = round(60 * 30 / 100)  # growth: 30% of 60
        assert rollover <= max_rollover

    def test_rollover_creates_transaction(self, engine):
        acct = WorkingDayAccount(
            business_id=11, plan="scale", working_days_remaining=100,
            annual_billing=True)
        engine.register_account(acct)
        engine.process_rollover(11)
        history = engine.get_transaction_history(11)
        assert any(t.reason == "rollover" for t in history)


# --- 268: Credit gifting between team members ---

class TestCreditGifting:
    def test_gift_working_days(self, engine, starter_account, pro_account):
        txn_s, txn_r = engine.gift_working_days(2, 1, 5)
        assert txn_s.delta == -5
        assert txn_r.delta == 5
        assert starter_account.working_days_remaining == 10
        assert pro_account.working_days_remaining == 15

    def test_gift_insufficient_working_days(self, engine, starter_account, pro_account):
        with pytest.raises(ValueError, match="Insufficient"):
            engine.gift_working_days(1, 2, 100)

    def test_gift_zero_raises(self, engine, starter_account, pro_account):
        with pytest.raises(ValueError, match="positive"):
            engine.gift_working_days(1, 2, 0)

    def test_gift_tracks_gifted_amounts(self, engine, starter_account, pro_account):
        engine.gift_working_days(2, 1, 3)
        assert pro_account.working_days_gifted_out == 3
        assert starter_account.working_days_gifted_in == 3


# --- 271: Dynamic pricing based on business size ---

class TestBusinessSizePricing:
    def test_solo_discount(self, engine):
        acct = WorkingDayAccount(
            business_id=20, plan="pro", working_days_remaining=20, employee_count=1)
        engine.register_account(acct)
        txn = engine.pay_per_cycle(20)
        base = PLAN_CATALOG["pro"]["cycle_price_cents"]
        assert txn.metadata["cost_cents"] < base  # solo gets 0.8x

    def test_enterprise_size_premium(self, engine):
        acct = WorkingDayAccount(
            business_id=21, plan="pro", working_days_remaining=20,
            employee_count=5000)
        engine.register_account(acct)
        txn = engine.pay_per_cycle(21)
        base = PLAN_CATALOG["pro"]["cycle_price_cents"]
        assert txn.metadata["cost_cents"] > base

    def test_small_business_neutral(self, engine):
        acct = WorkingDayAccount(
            business_id=22, plan="starter", working_days_remaining=5,
            employee_count=25)
        engine.register_account(acct)
        txn = engine.pay_per_cycle(22)
        base = PLAN_CATALOG["starter"]["cycle_price_cents"]
        assert txn.metadata["cost_cents"] == base  # 1.0x


# --- 274: ROI calculator ---

class TestROICalculator:
    def test_roi_positive(self, engine):
        roi = engine.calculate_roi("pro")
        assert roi.roi_pct > 0
        assert roi.payback_months > 0

    def test_roi_includes_features(self, engine):
        roi = engine.calculate_roi("growth")
        assert len(roi.features_used) > 0

    def test_roi_scales_with_hours(self, engine):
        roi_low = engine.calculate_roi("pro", hours_per_week_manual=5)
        roi_high = engine.calculate_roi("pro", hours_per_week_manual=40)
        assert roi_high.estimated_hours_saved > roi_low.estimated_hours_saved

    def test_roi_plan_comparison(self, engine):
        roi_s = engine.calculate_roi("starter")
        roi_e = engine.calculate_roi("enterprise")
        assert roi_s.monthly_cost_cents != roi_e.monthly_cost_cents


# --- 277: Startup/nonprofit discount program ---

class TestDiscountPrograms:
    def test_apply_startup_discount(self, engine, starter_account):
        result = engine.apply_discount_program(1, "startup")
        assert result["discount_pct"] == 30
        assert result["expires_at"] is not None

    def test_apply_nonprofit_perpetual(self, engine, starter_account):
        result = engine.apply_discount_program(1, "nonprofit")
        assert result["discount_pct"] == 40
        assert result["expires_at"] is None  # perpetual

    def test_ineligible_plan_raises(self, engine):
        acct = WorkingDayAccount(business_id=30, plan="enterprise", working_days_remaining=999)
        engine.register_account(acct)
        with pytest.raises(ValueError, match="not eligible"):
            engine.apply_discount_program(30, "startup")

    def test_unknown_program_raises(self, engine, starter_account):
        with pytest.raises(ValueError, match="Unknown"):
            engine.apply_discount_program(1, "fake_program")

    def test_discount_applied_to_pay_per_cycle(self, engine, starter_account):
        engine.apply_discount_program(1, "startup")
        txn = engine.pay_per_cycle(1)
        base = PLAN_CATALOG["starter"]["cycle_price_cents"]
        assert txn.metadata["cost_cents"] < base


# --- 280: Working day marketplace for unused working days ---

class TestCreditMarketplace:
    def test_create_listing(self, engine, pro_account):
        listing = engine.create_marketplace_listing(2, working_days=5, price_cents_per_working_day=500)
        assert listing.status == "active"
        assert listing.working_days == 5
        assert pro_account.marketplace_listed_working_days == 5

    def test_listing_exceeds_available(self, engine, starter_account):
        with pytest.raises(ValueError, match="available"):
            engine.create_marketplace_listing(1, working_days=100, price_cents_per_working_day=500)

    def test_purchase_listing(self, engine, starter_account, pro_account):
        listing = engine.create_marketplace_listing(2, working_days=5, price_cents_per_working_day=400)
        txn_s, txn_b = engine.purchase_marketplace_listing(listing, 1)
        assert txn_s.reason == "marketplace_sold"
        assert txn_b.reason == "marketplace_purchased"
        assert listing.status == "sold"
        assert starter_account.working_days_remaining == 10  # 5 + 5

    def test_purchase_inactive_listing(self, engine, starter_account, pro_account):
        listing = engine.create_marketplace_listing(2, working_days=3, price_cents_per_working_day=400)
        listing.status = "expired"
        with pytest.raises(ValueError, match="not active"):
            engine.purchase_marketplace_listing(listing, 1)

    def test_listing_zero_working_days_raises(self, engine, pro_account):
        with pytest.raises(ValueError, match="at least 1"):
            engine.create_marketplace_listing(2, working_days=0, price_cents_per_working_day=500)


# --- 283: Usage-based auto-upgrade suggestions ---

class TestAutoUpgrade:
    def test_suggest_upgrade_high_usage(self, engine):
        acct = WorkingDayAccount(
            business_id=40, plan="starter", working_days_remaining=0)
        engine.register_account(acct)
        suggestion = engine.suggest_upgrade(40)
        assert suggestion is not None
        assert suggestion.suggested_plan == "pro"
        assert len(suggestion.reasons) > 0

    def test_no_suggestion_low_usage(self, engine, starter_account):
        suggestion = engine.suggest_upgrade(1)
        assert suggestion is None

    def test_no_upgrade_top_tier(self, engine):
        acct = WorkingDayAccount(
            business_id=41, plan="enterprise", working_days_remaining=0)
        engine.register_account(acct)
        suggestion = engine.suggest_upgrade(41)
        assert suggestion is None

    def test_suggestion_confidence_range(self, engine):
        acct = WorkingDayAccount(
            business_id=42, plan="starter", working_days_remaining=1,
            metered_usage_cents=5000)
        engine.register_account(acct)
        suggestion = engine.suggest_upgrade(42)
        assert suggestion is not None
        assert 0 <= suggestion.confidence <= 1.0


# --- 286: Credit bonus for referrals ---

class TestReferralBonus:
    def test_referral_bonus(self, engine, starter_account, pro_account):
        txn_ref, txn_new = engine.process_referral(1, 2, bonus_working_days=5)
        assert txn_ref.delta == 5
        assert txn_new.delta == 5
        assert starter_account.working_days_remaining == 10
        assert pro_account.working_days_remaining == 25

    def test_referral_code_generated(self, engine, starter_account):
        assert starter_account.referral_code is not None
        assert len(starter_account.referral_code) == 12

    def test_referral_sets_referred_by(self, engine, starter_account, pro_account):
        engine.process_referral(1, 2)
        assert pro_account.referred_by == starter_account.referral_code


# --- 289: Variable pricing by feature complexity ---

class TestVariablePricing:
    def test_complex_feature_costs_more(self, engine):
        acct = WorkingDayAccount(
            business_id=50, plan="pro", working_days_remaining=20)
        engine.register_account(acct)
        # white_label has complexity 3.0
        txn = engine.consume_working_day(50, amount=1, feature="white_label")
        assert abs(txn.delta) >= 3  # 1 * 3.0 rounded

    def test_simple_feature_base_cost(self, engine):
        acct = WorkingDayAccount(
            business_id=51, plan="pro", working_days_remaining=20)
        engine.register_account(acct)
        txn = engine.consume_working_day(51, amount=1, feature="social_scheduling")
        assert abs(txn.delta) == 1  # 1 * 0.5 rounds to 1 (min 1)

    def test_no_feature_base_cost(self, engine, starter_account):
        txn = engine.consume_working_day(1, amount=1)
        assert abs(txn.delta) == 1


# --- 359: Usage-based pricing adjustment to prevent price churn ---

class TestPricingAdjustment:
    def test_loyalty_discount_after_1_year(self, engine, starter_account):
        result = engine.adjust_pricing_for_retention(1, months_subscribed=12)
        assert result["loyalty_discount_pct"] > 0
        assert result["adjusted_price_cents"] < result["base_price_cents"]

    def test_no_discount_new_customer(self, engine, starter_account):
        result = engine.adjust_pricing_for_retention(1, months_subscribed=1)
        assert result["loyalty_discount_pct"] == 0

    def test_loyalty_capped_at_15pct(self, engine, starter_account):
        result = engine.adjust_pricing_for_retention(1, months_subscribed=100)
        assert result["loyalty_discount_pct"] == 15


# --- 444: Credit expiration with notification ---

class TestCreditExpiration:
    def test_set_expiration(self, engine, starter_account):
        exp = engine.set_working_day_expiration(1, working_days=3, days=30)
        assert exp.working_days == 3
        assert not exp.expired

    def test_expiration_triggers_7d_notification(self, engine, starter_account):
        exp = engine.set_working_day_expiration(1, working_days=3, days=5)
        now = datetime.now(timezone.utc) + timedelta(days=1)
        results = engine.check_expirations(now)
        assert len(results) == 1
        assert results[0].notified_7d

    def test_expiration_triggers_1d_notification(self, engine, starter_account):
        exp = engine.set_working_day_expiration(1, working_days=3, days=1)
        now = datetime.now(timezone.utc) + timedelta(hours=12)
        results = engine.check_expirations(now)
        assert any(r.notified_1d for r in results)

    def test_expired_working_days_deducted(self, engine, starter_account):
        engine.set_working_day_expiration(1, working_days=3, days=1)
        now = datetime.now(timezone.utc) + timedelta(days=2)
        results = engine.check_expirations(now)
        assert any(r.expired for r in results)
        assert starter_account.working_days_remaining == 2  # 5 - 3

    def test_already_expired_not_reprocessed(self, engine, starter_account):
        engine.set_working_day_expiration(1, working_days=2, days=1)
        now = datetime.now(timezone.utc) + timedelta(days=2)
        engine.check_expirations(now)
        results = engine.check_expirations(now)
        assert len(results) == 0


# --- 447: Spending caps for enterprise accounts ---

class TestSpendingCaps:
    def test_set_spending_cap(self, engine, starter_account):
        engine.set_spending_cap(1, cap_cents=50000)
        assert starter_account.spending_cap_cents == 50000

    def test_spending_cap_enforced(self, engine):
        acct = WorkingDayAccount(
            business_id=60, plan="pro", working_days_remaining=100,
            spending_cap_cents=1000)
        engine.register_account(acct)
        # consume working days until cap hit
        with pytest.raises(ValueError, match="Spending cap"):
            for _ in range(20):
                engine.consume_working_day(60)

    def test_spending_remaining(self, engine, starter_account):
        engine.set_spending_cap(1, cap_cents=10000)
        engine.consume_working_day(1)
        remaining = engine.get_spending_remaining(1)
        assert remaining is not None
        assert remaining < 10000

    def test_no_cap_returns_none(self, engine, starter_account):
        assert engine.get_spending_remaining(1) is None


# --- 451: Prepaid working day packages with bonus working days ---

class TestPrepaidPackages:
    def test_purchase_package(self, engine, starter_account):
        txn = engine.purchase_prepaid_package(1, "pack_10")
        assert txn.delta == 11  # 10 + 1 bonus
        assert starter_account.working_days_remaining == 16  # 5 + 11

    def test_purchase_large_package(self, engine, starter_account):
        txn = engine.purchase_prepaid_package(1, "pack_100")
        assert txn.metadata["bonus_working_days"] == 20
        assert txn.delta == 120

    def test_unknown_package_raises(self, engine, starter_account):
        with pytest.raises(ValueError, match="Unknown package"):
            engine.purchase_prepaid_package(1, "pack_9999")

    def test_package_updates_bonus(self, engine, starter_account):
        engine.purchase_prepaid_package(1, "pack_25")
        assert starter_account.working_days_bonus == 3

    def test_package_updates_total_spent(self, engine, starter_account):
        engine.purchase_prepaid_package(1, "pack_50")
        assert starter_account.total_spent_cents == 42500


# --- 460: Working day purchase history and projections ---

class TestHistoryAndProjections:
    def test_transaction_history(self, engine, starter_account):
        engine.consume_working_day(1)
        engine.consume_working_day(1)
        history = engine.get_transaction_history(1)
        assert len(history) == 2

    def test_projection_with_usage(self, engine, starter_account):
        engine.consume_working_day(1)
        engine.consume_working_day(1)
        proj = engine.project_working_day_usage(1)
        assert proj["current_working_days"] == 3
        assert proj["avg_daily_usage"] > 0

    def test_projection_no_usage(self, engine, starter_account):
        proj = engine.project_working_day_usage(1)
        assert proj["projected_depletion_days"] is None
        assert proj["recommendation"] == "No usage data yet"

    def test_history_limit(self, engine, starter_account):
        for _ in range(5):
            engine.consume_working_day(1)
            starter_account.working_days_remaining += 2
        history = engine.get_transaction_history(1, limit=3)
        assert len(history) == 3


# --- 463: Credit usage reporting per feature ---

class TestUsageReporting:
    def test_usage_by_feature(self, engine):
        acct = WorkingDayAccount(
            business_id=70, plan="pro", working_days_remaining=50)
        engine.register_account(acct)
        engine.consume_working_day(70, feature="content_generation")
        engine.consume_working_day(70, feature="content_generation")
        engine.consume_working_day(70, feature="seo_optimization")
        report = engine.get_usage_by_feature(70)
        assert "content_generation" in report
        assert report["content_generation"]["transactions"] == 2
        assert "seo_optimization" in report

    def test_usage_no_feature_labeled_general(self, engine, starter_account):
        engine.consume_working_day(1)
        report = engine.get_usage_by_feature(1)
        assert "general" in report

    def test_usage_empty_for_new_account(self, engine, starter_account):
        report = engine.get_usage_by_feature(1)
        assert len(report) == 0


# --- Catalog / constant tests ---

class TestCatalogs:
    def test_plan_catalog_completeness(self):
        assert "starter" in PLAN_CATALOG
        assert "enterprise" in PLAN_CATALOG
        for plan in PLAN_CATALOG.values():
            assert "price_cents" in plan
            assert "working_days" in plan
            assert "features" in plan

    def test_demand_bands_cover_range(self):
        assert DEMAND_BANDS[0]["min_demand"] == 0
        assert DEMAND_BANDS[-1]["max_demand"] == 999999

    def test_prepaid_packages_have_bonus(self):
        for pkg in PREPAID_PACKAGES:
            assert pkg["bonus_working_days"] > 0

    def test_business_size_tiers_ordered(self):
        for i in range(len(BUSINESS_SIZE_TIERS) - 1):
            assert (BUSINESS_SIZE_TIERS[i]["max_employees"]
                    < BUSINESS_SIZE_TIERS[i + 1]["max_employees"])

    def test_feature_complexity_values(self):
        for feat, comp in FEATURE_COMPLEXITY.items():
            assert comp > 0

    def test_discount_programs_valid(self):
        for prog in DISCOUNT_PROGRAMS.values():
            assert 0 < prog["pct"] <= 100
            assert len(prog["eligible_plans"]) > 0
