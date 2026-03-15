"""Enterprise features: white-label branding, custom domains, reporting,
SLA tiers, analytics export, template marketplace, affiliate program,
customer showcase, premium certification, revenue dashboard, payment management,
receipts with tax calculation.

Covers: 402,408,413,417,421,426,431,435,439,440,454,457.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SLATier(str, Enum):
    STANDARD = "standard"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class TemplateStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    CERTIFIED = "certified"
    PUBLISHED = "published"
    REJECTED = "rejected"


class PaymentMethodType(str, Enum):
    CREDIT_CARD = "credit_card"
    BANK_TRANSFER = "bank_transfer"
    PAYPAL = "paypal"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WhiteLabelConfig:
    business_id: int
    brand_name: str
    primary_color: str = "#4f46e5"
    secondary_color: str = "#1a1a2e"
    logo_url: str | None = None
    favicon_url: str | None = None
    custom_css: str | None = None
    email_from_name: str | None = None
    powered_by_hidden: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CustomDomain:
    business_id: int
    domain: str
    status: str = "pending"  # pending|verifying|active|failed
    ssl_status: str = "pending"  # pending|provisioning|active|failed
    dns_records: list[dict[str, str]] = field(default_factory=list)
    verified_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EnterpriseReport:
    business_id: int
    report_type: str
    title: str
    metrics: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    export_formats: list[str] = field(default_factory=lambda: ["json", "csv", "pdf"])


@dataclass
class SLAConfig:
    business_id: int
    tier: str = "standard"
    uptime_guarantee_pct: float = 99.0
    response_time_hours: int = 24
    dedicated_support: bool = False
    priority_queue: bool = False
    custom_sla_terms: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalyticsExport:
    business_id: int
    export_type: str  # "full"|"metrics"|"content"|"cycles"
    format: str = "json"  # json|csv|pdf
    data: dict[str, Any] = field(default_factory=dict)
    row_count: int = 0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MarketplaceTemplate:
    id: str
    creator_business_id: int
    name: str
    description: str
    category: str
    price_cents: int = 0
    status: str = "draft"
    downloads: int = 0
    rating: float = 0.0
    revenue_share_pct: float = 70.0  # creator keeps 70%
    certified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AffiliateAccount:
    id: str
    business_id: int
    referral_code: str
    commission_pct: float = 15.0
    total_referrals: int = 0
    total_conversions: int = 0
    total_earnings_cents: int = 0
    pending_payout_cents: int = 0
    paid_out_cents: int = 0
    status: str = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AffiliateReferral:
    affiliate_id: str
    referred_business_id: int
    converted: bool = False
    conversion_plan: str | None = None
    commission_cents: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CustomerShowcase:
    id: str
    business_id: int
    business_name: str
    industry: str
    description: str
    website_url: str | None = None
    logo_url: str | None = None
    featured: bool = False
    approved: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PaymentMethod:
    id: str
    business_id: int
    method_type: str
    last_four: str
    expiry_month: int | None = None
    expiry_year: int | None = None
    is_default: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Receipt:
    id: str
    business_id: int
    amount_cents: int
    tax_cents: int
    total_cents: int
    currency: str = "usd"
    tax_rate_pct: float = 0.0
    tax_jurisdiction: str = ""
    description: str = ""
    line_items: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RevenueDashboardSnapshot:
    total_mrr_cents: int = 0
    total_arr_cents: int = 0
    active_subscriptions: int = 0
    trial_conversions: int = 0
    churn_rate_pct: float = 0.0
    avg_revenue_per_user_cents: int = 0
    total_credits_consumed: int = 0
    marketplace_revenue_cents: int = 0
    affiliate_payouts_cents: int = 0
    top_plans: dict[str, int] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Tax rate lookup
# ---------------------------------------------------------------------------

TAX_RATES: dict[str, float] = {
    "US-CA": 7.25, "US-NY": 8.0, "US-TX": 6.25, "US-FL": 6.0,
    "US-WA": 6.5, "US-DEFAULT": 0.0,
    "CA-ON": 13.0, "CA-BC": 12.0, "CA-QC": 14.975, "CA-AB": 5.0,
    "CA-DEFAULT": 5.0,
    "GB": 20.0, "DE": 19.0, "FR": 20.0, "AU": 10.0,
    "DEFAULT": 0.0,
}

SLA_TIERS: dict[str, dict[str, Any]] = {
    "standard": {
        "uptime_pct": 99.0, "response_hours": 24,
        "dedicated": False, "priority": False,
    },
    "premium": {
        "uptime_pct": 99.9, "response_hours": 4,
        "dedicated": False, "priority": True,
    },
    "enterprise": {
        "uptime_pct": 99.99, "response_hours": 1,
        "dedicated": True, "priority": True,
    },
}


# ---------------------------------------------------------------------------
# Enterprise Features Engine
# ---------------------------------------------------------------------------

class EnterpriseEngine:
    """Manages white-label, custom domains, reporting, SLA, exports,
    marketplace, affiliates, showcases, payments, and revenue dashboard."""

    def __init__(self) -> None:
        self._whitelabel: dict[int, WhiteLabelConfig] = {}
        self._domains: dict[int, CustomDomain] = {}
        self._reports: list[EnterpriseReport] = []
        self._sla: dict[int, SLAConfig] = {}
        self._exports: list[AnalyticsExport] = []
        self._templates: dict[str, MarketplaceTemplate] = {}
        self._affiliates: dict[str, AffiliateAccount] = {}
        self._affiliate_referrals: list[AffiliateReferral] = []
        self._showcases: dict[str, CustomerShowcase] = {}
        self._payment_methods: dict[int, list[PaymentMethod]] = {}
        self._receipts: list[Receipt] = []

    # -- white-label branding (402) --

    def configure_whitelabel(self, config: WhiteLabelConfig) -> WhiteLabelConfig:
        self._whitelabel[config.business_id] = config
        return config

    def get_whitelabel(self, business_id: int) -> WhiteLabelConfig | None:
        return self._whitelabel.get(business_id)

    def update_whitelabel(
        self, business_id: int, **kwargs: Any,
    ) -> WhiteLabelConfig:
        cfg = self._whitelabel.get(business_id)
        if not cfg:
            raise ValueError(f"No white-label config for business {business_id}")
        for k, v in kwargs.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        cfg.updated_at = datetime.now(timezone.utc)
        return cfg

    # -- custom domain support (408) --

    def register_custom_domain(
        self, business_id: int, domain: str,
    ) -> CustomDomain:
        cd = CustomDomain(
            business_id=business_id, domain=domain,
            dns_records=[
                {"type": "CNAME", "name": domain,
                 "value": "proxy.arclane.cloud", "ttl": "3600"},
                {"type": "TXT", "name": f"_arclane.{domain}",
                 "value": f"arclane-verify={business_id}", "ttl": "3600"},
            ],
        )
        self._domains[business_id] = cd
        return cd

    def verify_domain(self, business_id: int) -> CustomDomain:
        cd = self._domains.get(business_id)
        if not cd:
            raise ValueError(f"No custom domain for business {business_id}")
        # In production, would check DNS records
        cd.status = "active"
        cd.ssl_status = "active"
        cd.verified_at = datetime.now(timezone.utc)
        return cd

    def get_custom_domain(self, business_id: int) -> CustomDomain | None:
        return self._domains.get(business_id)

    # -- enterprise reporting with custom metrics (413) --

    def generate_report(
        self, business_id: int, report_type: str, title: str,
        metrics: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> EnterpriseReport:
        report = EnterpriseReport(
            business_id=business_id, report_type=report_type,
            title=title, metrics=metrics or {},
            filters=filters or {},
        )
        self._reports.append(report)
        return report

    def get_reports(
        self, business_id: int, report_type: str | None = None,
    ) -> list[EnterpriseReport]:
        reports = [r for r in self._reports if r.business_id == business_id]
        if report_type:
            reports = [r for r in reports if r.report_type == report_type]
        return reports

    # -- enterprise SLA tier with priority support (417) --

    def configure_sla(
        self, business_id: int, tier: str,
    ) -> SLAConfig:
        tier_info = SLA_TIERS.get(tier)
        if not tier_info:
            raise ValueError(f"Unknown SLA tier: {tier}")
        sla = SLAConfig(
            business_id=business_id, tier=tier,
            uptime_guarantee_pct=tier_info["uptime_pct"],
            response_time_hours=tier_info["response_hours"],
            dedicated_support=tier_info["dedicated"],
            priority_queue=tier_info["priority"],
        )
        self._sla[business_id] = sla
        return sla

    def get_sla(self, business_id: int) -> SLAConfig | None:
        return self._sla.get(business_id)

    # -- enterprise analytics export (421) --

    def export_analytics(
        self, business_id: int, export_type: str = "full",
        format: str = "json",
        data: dict[str, Any] | None = None,
    ) -> AnalyticsExport:
        export_data = data or {
            "cycles": [], "content": [], "metrics": [],
            "credits": {"used": 0, "remaining": 0},
        }
        row_count = sum(
            len(v) for v in export_data.values() if isinstance(v, list))
        export = AnalyticsExport(
            business_id=business_id, export_type=export_type,
            format=format, data=export_data, row_count=row_count,
        )
        self._exports.append(export)
        return export

    # -- template marketplace with creator revenue share (426) --

    def submit_template(
        self, creator_business_id: int, name: str, description: str,
        category: str, price_cents: int = 0,
    ) -> MarketplaceTemplate:
        tmpl_id = hashlib.sha256(
            f"{creator_business_id}-{name}-{time.time()}".encode()
        ).hexdigest()[:12]
        tmpl = MarketplaceTemplate(
            id=tmpl_id, creator_business_id=creator_business_id,
            name=name, description=description, category=category,
            price_cents=price_cents, status="pending_review",
        )
        self._templates[tmpl_id] = tmpl
        return tmpl

    def certify_template(self, template_id: str) -> MarketplaceTemplate:
        tmpl = self._templates.get(template_id)
        if not tmpl:
            raise ValueError(f"Template {template_id} not found")
        tmpl.status = "certified"
        tmpl.certified = True
        return tmpl

    def publish_template(self, template_id: str) -> MarketplaceTemplate:
        tmpl = self._templates.get(template_id)
        if not tmpl:
            raise ValueError(f"Template {template_id} not found")
        if not tmpl.certified:
            raise ValueError("Template must be certified before publishing")
        tmpl.status = "published"
        return tmpl

    def purchase_template(
        self, template_id: str, buyer_business_id: int,
    ) -> dict[str, Any]:
        tmpl = self._templates.get(template_id)
        if not tmpl:
            raise ValueError(f"Template {template_id} not found")
        if tmpl.status != "published":
            raise ValueError("Template is not published")
        tmpl.downloads += 1
        creator_share = round(tmpl.price_cents * tmpl.revenue_share_pct / 100)
        platform_share = tmpl.price_cents - creator_share
        return {
            "template_id": template_id,
            "buyer_business_id": buyer_business_id,
            "total_cents": tmpl.price_cents,
            "creator_share_cents": creator_share,
            "platform_share_cents": platform_share,
        }

    def get_marketplace_templates(
        self, category: str | None = None, certified_only: bool = False,
    ) -> list[MarketplaceTemplate]:
        templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.category == category]
        if certified_only:
            templates = [t for t in templates if t.certified]
        templates = [t for t in templates if t.status == "published"]
        return templates

    # -- affiliate program with tracking (431) --

    def create_affiliate(
        self, business_id: int, commission_pct: float = 15.0,
    ) -> AffiliateAccount:
        aff_id = hashlib.sha256(
            f"aff-{business_id}-{time.time()}".encode()
        ).hexdigest()[:12]
        ref_code = hashlib.sha256(
            f"ref-{business_id}".encode()
        ).hexdigest()[:8]
        aff = AffiliateAccount(
            id=aff_id, business_id=business_id,
            referral_code=ref_code, commission_pct=commission_pct,
        )
        self._affiliates[aff_id] = aff
        return aff

    def track_affiliate_referral(
        self, affiliate_id: str, referred_business_id: int,
    ) -> AffiliateReferral:
        aff = self._affiliates.get(affiliate_id)
        if not aff:
            raise ValueError(f"Affiliate {affiliate_id} not found")
        ref = AffiliateReferral(
            affiliate_id=affiliate_id,
            referred_business_id=referred_business_id,
        )
        aff.total_referrals += 1
        self._affiliate_referrals.append(ref)
        return ref

    def convert_affiliate_referral(
        self, affiliate_id: str, referred_business_id: int,
        plan_price_cents: int,
    ) -> AffiliateReferral | None:
        aff = self._affiliates.get(affiliate_id)
        if not aff:
            return None
        for ref in self._affiliate_referrals:
            if (ref.affiliate_id == affiliate_id
                    and ref.referred_business_id == referred_business_id
                    and not ref.converted):
                ref.converted = True
                commission = round(plan_price_cents * aff.commission_pct / 100)
                ref.commission_cents = commission
                aff.total_conversions += 1
                aff.total_earnings_cents += commission
                aff.pending_payout_cents += commission
                return ref
        return None

    def get_affiliate_stats(self, affiliate_id: str) -> dict[str, Any]:
        aff = self._affiliates.get(affiliate_id)
        if not aff:
            raise ValueError(f"Affiliate {affiliate_id} not found")
        return {
            "affiliate_id": aff.id,
            "referral_code": aff.referral_code,
            "total_referrals": aff.total_referrals,
            "total_conversions": aff.total_conversions,
            "conversion_rate": (aff.total_conversions / max(aff.total_referrals, 1)) * 100,
            "total_earnings_cents": aff.total_earnings_cents,
            "pending_payout_cents": aff.pending_payout_cents,
            "paid_out_cents": aff.paid_out_cents,
        }

    # -- customer showcase/portfolio directory (435) --

    def submit_showcase(
        self, business_id: int, business_name: str, industry: str,
        description: str, website_url: str | None = None,
    ) -> CustomerShowcase:
        sc_id = hashlib.sha256(
            f"sc-{business_id}-{time.time()}".encode()
        ).hexdigest()[:12]
        sc = CustomerShowcase(
            id=sc_id, business_id=business_id,
            business_name=business_name, industry=industry,
            description=description, website_url=website_url,
        )
        self._showcases[sc_id] = sc
        return sc

    def approve_showcase(self, showcase_id: str) -> CustomerShowcase:
        sc = self._showcases.get(showcase_id)
        if not sc:
            raise ValueError(f"Showcase {showcase_id} not found")
        sc.approved = True
        return sc

    def feature_showcase(self, showcase_id: str) -> CustomerShowcase:
        sc = self._showcases.get(showcase_id)
        if not sc:
            raise ValueError(f"Showcase {showcase_id} not found")
        sc.featured = True
        return sc

    def get_showcases(
        self, industry: str | None = None, featured_only: bool = False,
    ) -> list[CustomerShowcase]:
        items = [s for s in self._showcases.values() if s.approved]
        if industry:
            items = [s for s in items if s.industry == industry]
        if featured_only:
            items = [s for s in items if s.featured]
        return items

    # -- premium template certification program (439) --

    def request_certification(self, template_id: str) -> dict[str, Any]:
        tmpl = self._templates.get(template_id)
        if not tmpl:
            raise ValueError(f"Template {template_id} not found")
        checks = {
            "has_description": len(tmpl.description) >= 20,
            "has_category": bool(tmpl.category),
            "has_name": len(tmpl.name) >= 3,
            "price_set": tmpl.price_cents >= 0,
        }
        passed = all(checks.values())
        if passed:
            tmpl.status = "pending_review"
        return {
            "template_id": template_id,
            "checks": checks,
            "passed": passed,
            "status": tmpl.status,
        }

    # -- real-time revenue dashboard for Chris (440) --

    def generate_revenue_dashboard(
        self,
        subscriptions: list[dict[str, Any]] | None = None,
        credits_data: dict[str, Any] | None = None,
    ) -> RevenueDashboardSnapshot:
        subs = subscriptions or []
        total_mrr = sum(s.get("price_cents", 0) for s in subs)
        plan_counts: dict[str, int] = {}
        for s in subs:
            plan = s.get("plan", "unknown")
            plan_counts[plan] = plan_counts.get(plan, 0) + 1
        active = len([s for s in subs if s.get("status") == "active"])
        trials = len([s for s in subs if s.get("is_trial")])
        converted = len([s for s in subs
                         if s.get("is_trial") and s.get("converted")])
        churned = len([s for s in subs if s.get("status") == "churned"])
        churn_rate = (churned / max(active + churned, 1)) * 100
        arpu = total_mrr // max(active, 1)
        credits = credits_data or {}
        mp_revenue = sum(
            t.price_cents * t.downloads
            for t in self._templates.values()
            if t.status == "published"
        )
        aff_payouts = sum(a.paid_out_cents for a in self._affiliates.values())
        return RevenueDashboardSnapshot(
            total_mrr_cents=total_mrr,
            total_arr_cents=total_mrr * 12,
            active_subscriptions=active,
            trial_conversions=converted,
            churn_rate_pct=round(churn_rate, 2),
            avg_revenue_per_user_cents=arpu,
            total_credits_consumed=credits.get("consumed", 0),
            marketplace_revenue_cents=mp_revenue,
            affiliate_payouts_cents=aff_payouts,
            top_plans=plan_counts,
        )

    # -- payment method management (454) --

    def add_payment_method(
        self, business_id: int, method_type: str,
        last_four: str, expiry_month: int | None = None,
        expiry_year: int | None = None,
    ) -> PaymentMethod:
        pm_id = hashlib.sha256(
            f"pm-{business_id}-{last_four}-{time.time()}".encode()
        ).hexdigest()[:12]
        existing = self._payment_methods.get(business_id, [])
        is_default = len(existing) == 0
        pm = PaymentMethod(
            id=pm_id, business_id=business_id,
            method_type=method_type, last_four=last_four,
            expiry_month=expiry_month, expiry_year=expiry_year,
            is_default=is_default,
        )
        if business_id not in self._payment_methods:
            self._payment_methods[business_id] = []
        self._payment_methods[business_id].append(pm)
        return pm

    def set_default_payment_method(
        self, business_id: int, payment_method_id: str,
    ) -> PaymentMethod:
        methods = self._payment_methods.get(business_id, [])
        target = None
        for m in methods:
            if m.id == payment_method_id:
                target = m
            m.is_default = False
        if not target:
            raise ValueError(f"Payment method {payment_method_id} not found")
        target.is_default = True
        return target

    def get_payment_methods(self, business_id: int) -> list[PaymentMethod]:
        return self._payment_methods.get(business_id, [])

    def remove_payment_method(
        self, business_id: int, payment_method_id: str,
    ) -> bool:
        methods = self._payment_methods.get(business_id, [])
        for i, m in enumerate(methods):
            if m.id == payment_method_id:
                if m.is_default and len(methods) > 1:
                    raise ValueError("Cannot remove default payment method")
                methods.pop(i)
                return True
        return False

    # -- automatic receipts with tax calculation (457) --

    def generate_receipt(
        self, business_id: int, amount_cents: int,
        description: str, jurisdiction: str = "DEFAULT",
        line_items: list[dict[str, Any]] | None = None,
    ) -> Receipt:
        tax_rate = TAX_RATES.get(jurisdiction, TAX_RATES["DEFAULT"])
        tax_cents = round(amount_cents * tax_rate / 100)
        total_cents = amount_cents + tax_cents
        receipt_id = hashlib.sha256(
            f"rcpt-{business_id}-{time.time()}".encode()
        ).hexdigest()[:12]
        receipt = Receipt(
            id=receipt_id, business_id=business_id,
            amount_cents=amount_cents, tax_cents=tax_cents,
            total_cents=total_cents, tax_rate_pct=tax_rate,
            tax_jurisdiction=jurisdiction, description=description,
            line_items=line_items or [{"description": description,
                                       "amount_cents": amount_cents}],
        )
        self._receipts.append(receipt)
        return receipt

    def get_receipts(
        self, business_id: int, limit: int = 50,
    ) -> list[Receipt]:
        return [r for r in self._receipts
                if r.business_id == business_id][-limit:]
