"""Tests for enterprise features: white-label, custom domains, reporting,
SLA, exports, marketplace, affiliates, showcases, payments, receipts,
revenue dashboard.

Covers items: 402,408,413,417,421,426,431,435,439,440,454,457.
"""

from datetime import datetime, timezone

import pytest

from arclane.enterprise.features import (
    AffiliateAccount,
    AffiliateReferral,
    AnalyticsExport,
    CustomDomain,
    CustomerShowcase,
    EnterpriseEngine,
    EnterpriseReport,
    MarketplaceTemplate,
    PaymentMethod,
    Receipt,
    RevenueDashboardSnapshot,
    SLAConfig,
    SLA_TIERS,
    TAX_RATES,
    WhiteLabelConfig,
)


@pytest.fixture
def engine():
    return EnterpriseEngine()


# --- 402: White-label branding per tenant ---

class TestWhiteLabel:
    def test_configure_whitelabel(self, engine):
        cfg = WhiteLabelConfig(
            business_id=1, brand_name="AcmeCorp",
            primary_color="#ff0000", logo_url="https://example.com/logo.png")
        result = engine.configure_whitelabel(cfg)
        assert result.brand_name == "AcmeCorp"
        assert result.primary_color == "#ff0000"

    def test_get_whitelabel(self, engine):
        cfg = WhiteLabelConfig(business_id=1, brand_name="Acme")
        engine.configure_whitelabel(cfg)
        result = engine.get_whitelabel(1)
        assert result is not None
        assert result.brand_name == "Acme"

    def test_update_whitelabel(self, engine):
        cfg = WhiteLabelConfig(business_id=1, brand_name="Old")
        engine.configure_whitelabel(cfg)
        result = engine.update_whitelabel(1, brand_name="New", primary_color="#00ff00")
        assert result.brand_name == "New"
        assert result.primary_color == "#00ff00"

    def test_update_nonexistent_raises(self, engine):
        with pytest.raises(ValueError, match="No white-label"):
            engine.update_whitelabel(999, brand_name="X")

    def test_whitelabel_defaults(self, engine):
        cfg = WhiteLabelConfig(business_id=2, brand_name="Default")
        engine.configure_whitelabel(cfg)
        result = engine.get_whitelabel(2)
        assert result.primary_color == "#4f46e5"
        assert result.powered_by_hidden is False

    def test_hidden_powered_by(self, engine):
        cfg = WhiteLabelConfig(
            business_id=3, brand_name="Premium", powered_by_hidden=True)
        engine.configure_whitelabel(cfg)
        assert engine.get_whitelabel(3).powered_by_hidden


# --- 408: Custom domain support per tenant ---

class TestCustomDomain:
    def test_register_domain(self, engine):
        cd = engine.register_custom_domain(1, "app.example.com")
        assert cd.domain == "app.example.com"
        assert cd.status == "pending"
        assert len(cd.dns_records) == 2

    def test_verify_domain(self, engine):
        engine.register_custom_domain(1, "app.example.com")
        cd = engine.verify_domain(1)
        assert cd.status == "active"
        assert cd.ssl_status == "active"
        assert cd.verified_at is not None

    def test_verify_nonexistent_raises(self, engine):
        with pytest.raises(ValueError, match="No custom domain"):
            engine.verify_domain(999)

    def test_get_domain(self, engine):
        engine.register_custom_domain(1, "my.domain.com")
        assert engine.get_custom_domain(1) is not None
        assert engine.get_custom_domain(999) is None

    def test_dns_records_correct(self, engine):
        cd = engine.register_custom_domain(1, "test.com")
        cname = [r for r in cd.dns_records if r["type"] == "CNAME"]
        txt = [r for r in cd.dns_records if r["type"] == "TXT"]
        assert len(cname) == 1
        assert len(txt) == 1
        assert "arclane.cloud" in cname[0]["value"]


# --- 413: Enterprise reporting with custom metrics ---

class TestEnterpriseReporting:
    def test_generate_report(self, engine):
        report = engine.generate_report(
            business_id=1, report_type="monthly",
            title="March Report",
            metrics={"revenue": 10000, "cycles": 50})
        assert report.title == "March Report"
        assert report.metrics["revenue"] == 10000

    def test_get_reports_by_type(self, engine):
        engine.generate_report(1, "monthly", "M1")
        engine.generate_report(1, "weekly", "W1")
        engine.generate_report(1, "monthly", "M2")
        monthly = engine.get_reports(1, report_type="monthly")
        assert len(monthly) == 2

    def test_report_export_formats(self, engine):
        report = engine.generate_report(1, "custom", "Test")
        assert "json" in report.export_formats
        assert "csv" in report.export_formats

    def test_report_with_filters(self, engine):
        report = engine.generate_report(
            1, "filtered", "Filtered",
            filters={"date_range": "2026-03-01/2026-03-31"})
        assert "date_range" in report.filters


# --- 417: Enterprise SLA tier with priority support ---

class TestSLATiers:
    def test_configure_standard_sla(self, engine):
        sla = engine.configure_sla(1, "standard")
        assert sla.uptime_guarantee_pct == 99.0
        assert not sla.dedicated_support
        assert not sla.priority_queue

    def test_configure_enterprise_sla(self, engine):
        sla = engine.configure_sla(1, "enterprise")
        assert sla.uptime_guarantee_pct == 99.99
        assert sla.dedicated_support
        assert sla.priority_queue
        assert sla.response_time_hours == 1

    def test_unknown_tier_raises(self, engine):
        with pytest.raises(ValueError, match="Unknown SLA tier"):
            engine.configure_sla(1, "platinum")

    def test_get_sla(self, engine):
        engine.configure_sla(1, "premium")
        sla = engine.get_sla(1)
        assert sla is not None
        assert sla.tier == "premium"

    def test_sla_tiers_constant(self):
        assert "standard" in SLA_TIERS
        assert "enterprise" in SLA_TIERS


# --- 421: Enterprise analytics export ---

class TestAnalyticsExport:
    def test_export_full(self, engine):
        export = engine.export_analytics(
            1, export_type="full",
            data={"cycles": [1, 2, 3], "content": [4, 5]})
        assert export.row_count == 5  # 3 + 2

    def test_export_default_data(self, engine):
        export = engine.export_analytics(1)
        assert "cycles" in export.data
        assert export.format == "json"

    def test_export_csv_format(self, engine):
        export = engine.export_analytics(1, format="csv")
        assert export.format == "csv"


# --- 426: Template marketplace with creator revenue share ---

class TestTemplateMarketplace:
    def test_submit_template(self, engine):
        tmpl = engine.submit_template(
            1, "Blog Starter", "A great blog template",
            "content", price_cents=4900)
        assert tmpl.status == "pending_review"
        assert tmpl.price_cents == 4900

    def test_certify_and_publish(self, engine):
        tmpl = engine.submit_template(1, "T1", "desc", "saas", price_cents=2900)
        engine.certify_template(tmpl.id)
        result = engine.publish_template(tmpl.id)
        assert result.status == "published"
        assert result.certified

    def test_publish_uncertified_raises(self, engine):
        tmpl = engine.submit_template(1, "T2", "desc", "landing")
        with pytest.raises(ValueError, match="certified"):
            engine.publish_template(tmpl.id)

    def test_purchase_template_revenue_share(self, engine):
        tmpl = engine.submit_template(1, "T3", "great template", "ecommerce",
                                      price_cents=5000)
        engine.certify_template(tmpl.id)
        engine.publish_template(tmpl.id)
        result = engine.purchase_template(tmpl.id, buyer_business_id=2)
        assert result["creator_share_cents"] == 3500  # 70%
        assert result["platform_share_cents"] == 1500

    def test_purchase_unpublished_raises(self, engine):
        tmpl = engine.submit_template(1, "T4", "desc", "misc")
        with pytest.raises(ValueError, match="not published"):
            engine.purchase_template(tmpl.id, buyer_business_id=2)

    def test_get_marketplace_published_only(self, engine):
        t1 = engine.submit_template(1, "T5", "d", "a", price_cents=100)
        t2 = engine.submit_template(1, "T6", "d", "a", price_cents=200)
        engine.certify_template(t1.id)
        engine.publish_template(t1.id)
        templates = engine.get_marketplace_templates()
        assert len(templates) == 1

    def test_get_marketplace_by_category(self, engine):
        for cat in ["content", "saas", "content"]:
            t = engine.submit_template(1, f"T-{cat}", "d", cat)
            engine.certify_template(t.id)
            engine.publish_template(t.id)
        results = engine.get_marketplace_templates(category="content")
        assert len(results) == 2

    def test_nonexistent_template_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.certify_template("fake_id")


# --- 431: Affiliate program with tracking ---

class TestAffiliateProgram:
    def test_create_affiliate(self, engine):
        aff = engine.create_affiliate(1, commission_pct=20.0)
        assert aff.commission_pct == 20.0
        assert aff.referral_code

    def test_track_referral(self, engine):
        aff = engine.create_affiliate(1)
        ref = engine.track_affiliate_referral(aff.id, referred_business_id=2)
        assert ref.affiliate_id == aff.id
        assert aff.total_referrals == 1

    def test_convert_referral(self, engine):
        aff = engine.create_affiliate(1, commission_pct=15.0)
        engine.track_affiliate_referral(aff.id, 2)
        ref = engine.convert_affiliate_referral(aff.id, 2, plan_price_cents=9900)
        assert ref is not None
        assert ref.converted
        assert ref.commission_cents == 1485  # 15% of 9900

    def test_affiliate_stats(self, engine):
        aff = engine.create_affiliate(1)
        engine.track_affiliate_referral(aff.id, 2)
        engine.track_affiliate_referral(aff.id, 3)
        engine.convert_affiliate_referral(aff.id, 2, 4900)
        stats = engine.get_affiliate_stats(aff.id)
        assert stats["total_referrals"] == 2
        assert stats["total_conversions"] == 1
        assert stats["conversion_rate"] == 50.0

    def test_unknown_affiliate_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.track_affiliate_referral("fake", 1)

    def test_affiliate_stats_unknown_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.get_affiliate_stats("fake")


# --- 435: Customer showcase/portfolio directory ---

class TestCustomerShowcase:
    def test_submit_showcase(self, engine):
        sc = engine.submit_showcase(1, "AcmeCorp", "saas",
                                    "We build great software")
        assert sc.business_name == "AcmeCorp"
        assert not sc.approved

    def test_approve_and_list(self, engine):
        sc = engine.submit_showcase(1, "B", "ecommerce", "Shop")
        engine.approve_showcase(sc.id)
        showcases = engine.get_showcases()
        assert len(showcases) == 1

    def test_feature_showcase(self, engine):
        sc = engine.submit_showcase(1, "C", "fintech", "Money")
        engine.approve_showcase(sc.id)
        engine.feature_showcase(sc.id)
        featured = engine.get_showcases(featured_only=True)
        assert len(featured) == 1

    def test_filter_by_industry(self, engine):
        for ind in ["saas", "ecommerce", "saas"]:
            sc = engine.submit_showcase(1, f"B-{ind}", ind, "desc")
            engine.approve_showcase(sc.id)
        saas = engine.get_showcases(industry="saas")
        assert len(saas) == 2

    def test_unapproved_not_listed(self, engine):
        engine.submit_showcase(1, "X", "tech", "desc")
        assert len(engine.get_showcases()) == 0


# --- 439: Premium template certification program ---

class TestCertificationProgram:
    def test_certification_passes(self, engine):
        tmpl = engine.submit_template(
            1, "Good Template", "A well-described template for blogs",
            "content", price_cents=2900)
        result = engine.request_certification(tmpl.id)
        assert result["passed"]
        assert all(result["checks"].values())

    def test_certification_fails_short_name(self, engine):
        tmpl = engine.submit_template(1, "AB", "Short description that is long enough",
                                      "content", price_cents=0)
        result = engine.request_certification(tmpl.id)
        assert not result["passed"]
        assert not result["checks"]["has_name"]

    def test_certification_fails_short_description(self, engine):
        tmpl = engine.submit_template(1, "ValidName", "Short", "content")
        result = engine.request_certification(tmpl.id)
        assert not result["passed"]
        assert not result["checks"]["has_description"]

    def test_nonexistent_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.request_certification("fake_id")


# --- 440: Real-time revenue dashboard for Chris ---

class TestRevenueDashboard:
    def test_empty_dashboard(self, engine):
        snap = engine.generate_revenue_dashboard()
        assert snap.total_mrr_cents == 0
        assert snap.active_subscriptions == 0

    def test_dashboard_with_subscriptions(self, engine):
        subs = [
            {"plan": "pro", "price_cents": 6900, "status": "active",
             "is_trial": False, "converted": False},
            {"plan": "growth", "price_cents": 14900, "status": "active",
             "is_trial": False, "converted": False},
            {"plan": "starter", "price_cents": 4900, "status": "churned",
             "is_trial": False, "converted": False},
        ]
        snap = engine.generate_revenue_dashboard(subscriptions=subs)
        assert snap.total_mrr_cents == 26700
        assert snap.active_subscriptions == 2
        assert snap.churn_rate_pct > 0

    def test_dashboard_arr_calculation(self, engine):
        subs = [{"plan": "pro", "price_cents": 10000, "status": "active",
                 "is_trial": False, "converted": False}]
        snap = engine.generate_revenue_dashboard(subscriptions=subs)
        assert snap.total_arr_cents == 120000

    def test_dashboard_plan_distribution(self, engine):
        subs = [
            {"plan": "pro", "price_cents": 6900, "status": "active",
             "is_trial": False, "converted": False},
            {"plan": "pro", "price_cents": 6900, "status": "active",
             "is_trial": False, "converted": False},
            {"plan": "growth", "price_cents": 14900, "status": "active",
             "is_trial": False, "converted": False},
        ]
        snap = engine.generate_revenue_dashboard(subscriptions=subs)
        assert snap.top_plans["pro"] == 2
        assert snap.top_plans["growth"] == 1

    def test_dashboard_trial_conversions(self, engine):
        subs = [
            {"plan": "pro", "price_cents": 6900, "status": "active",
             "is_trial": True, "converted": True},
            {"plan": "pro", "price_cents": 6900, "status": "active",
             "is_trial": True, "converted": False},
        ]
        snap = engine.generate_revenue_dashboard(subscriptions=subs)
        assert snap.trial_conversions == 1


# --- 454: Payment method management dashboard ---

class TestPaymentMethods:
    def test_add_payment_method(self, engine):
        pm = engine.add_payment_method(1, "credit_card", "4242",
                                       expiry_month=12, expiry_year=2027)
        assert pm.last_four == "4242"
        assert pm.is_default  # first method is default

    def test_add_second_not_default(self, engine):
        engine.add_payment_method(1, "credit_card", "4242")
        pm2 = engine.add_payment_method(1, "credit_card", "1234")
        assert not pm2.is_default

    def test_set_default(self, engine):
        pm1 = engine.add_payment_method(1, "credit_card", "4242")
        pm2 = engine.add_payment_method(1, "credit_card", "1234")
        result = engine.set_default_payment_method(1, pm2.id)
        assert result.is_default
        assert not pm1.is_default

    def test_set_default_nonexistent_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.set_default_payment_method(1, "fake_id")

    def test_get_payment_methods(self, engine):
        engine.add_payment_method(1, "credit_card", "4242")
        engine.add_payment_method(1, "paypal", "9999")
        methods = engine.get_payment_methods(1)
        assert len(methods) == 2

    def test_remove_payment_method(self, engine):
        pm = engine.add_payment_method(1, "credit_card", "4242")
        # single method can be removed
        removed = engine.remove_payment_method(1, pm.id)
        assert removed

    def test_remove_default_with_others_raises(self, engine):
        pm1 = engine.add_payment_method(1, "credit_card", "4242")
        engine.add_payment_method(1, "credit_card", "1234")
        with pytest.raises(ValueError, match="Cannot remove default"):
            engine.remove_payment_method(1, pm1.id)

    def test_empty_payment_methods(self, engine):
        assert engine.get_payment_methods(999) == []


# --- 457: Automatic receipts with tax calculation ---

class TestReceipts:
    def test_generate_receipt_no_tax(self, engine):
        receipt = engine.generate_receipt(
            1, 4900, "Pro subscription", jurisdiction="US-DEFAULT")
        assert receipt.tax_cents == 0
        assert receipt.total_cents == 4900

    def test_generate_receipt_with_tax(self, engine):
        receipt = engine.generate_receipt(
            1, 10000, "Growth subscription", jurisdiction="CA-ON")
        assert receipt.tax_rate_pct == 13.0
        assert receipt.tax_cents == 1300
        assert receipt.total_cents == 11300

    def test_receipt_uk_vat(self, engine):
        receipt = engine.generate_receipt(1, 5000, "Plan", jurisdiction="GB")
        assert receipt.tax_rate_pct == 20.0
        assert receipt.tax_cents == 1000

    def test_receipt_line_items(self, engine):
        items = [
            {"description": "Pro plan", "amount_cents": 6900},
            {"description": "Credit pack", "amount_cents": 2500},
        ]
        receipt = engine.generate_receipt(
            1, 9400, "Combined", line_items=items)
        assert len(receipt.line_items) == 2

    def test_get_receipts(self, engine):
        engine.generate_receipt(1, 100, "A")
        engine.generate_receipt(1, 200, "B")
        engine.generate_receipt(2, 300, "C")
        receipts = engine.get_receipts(1)
        assert len(receipts) == 2

    def test_default_jurisdiction(self, engine):
        receipt = engine.generate_receipt(1, 1000, "Test", jurisdiction="UNKNOWN")
        assert receipt.tax_cents == 0  # defaults to 0

    def test_tax_rates_constant(self):
        assert "CA-ON" in TAX_RATES
        assert "GB" in TAX_RATES
        assert "DEFAULT" in TAX_RATES
