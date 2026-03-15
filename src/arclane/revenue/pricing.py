"""Credit-based metered billing, pay-per-cycle, usage caps, dynamic pricing,
credit rollover, gifting, business-size pricing, ROI calculator, discounts,
credit marketplace, auto-upgrade, referral bonuses, variable pricing,
credit expiration, spending caps, prepaid packages, usage reporting.

Covers: 253,256,259,262,265,268,271,274,277,280,283,286,289,359,444,447,451,460,463.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PlanTier(str, Enum):
    STARTER = "starter"
    PRO = "pro"
    GROWTH = "growth"
    SCALE = "scale"
    ENTERPRISE = "enterprise"


class BillingMode(str, Enum):
    SUBSCRIPTION = "subscription"
    PAY_PER_CYCLE = "pay_per_cycle"
    METERED = "metered"


class DiscountProgram(str, Enum):
    STARTUP = "startup"
    NONPROFIT = "nonprofit"
    EDUCATION = "education"


# ---------------------------------------------------------------------------
# Catalogs / constants
# ---------------------------------------------------------------------------

PLAN_CATALOG: dict[str, dict[str, Any]] = {
    "starter": {
        "price_cents": 4900, "credits": 5, "name": "Starter",
        "cycle_price_cents": 1200, "metered_rate_cents": 1000,
        "annual_rollover_pct": 0,
        "features": {"basic_analytics", "content_generation", "single_user"},
    },
    "pro": {
        "price_cents": 6900, "credits": 20, "name": "Pro",
        "cycle_price_cents": 900, "metered_rate_cents": 800,
        "annual_rollover_pct": 20,
        "features": {"basic_analytics", "advanced_analytics", "content_generation",
                     "social_scheduling", "seo_optimization", "team_3"},
    },
    "growth": {
        "price_cents": 14900, "credits": 60, "name": "Growth",
        "cycle_price_cents": 700, "metered_rate_cents": 600,
        "annual_rollover_pct": 30,
        "features": {"basic_analytics", "advanced_analytics", "content_generation",
                     "social_scheduling", "seo_optimization", "ab_testing",
                     "custom_reports", "team_10"},
    },
    "scale": {
        "price_cents": 29900, "credits": 150, "name": "Scale",
        "cycle_price_cents": 500, "metered_rate_cents": 400,
        "annual_rollover_pct": 40,
        "features": {"basic_analytics", "advanced_analytics", "content_generation",
                     "social_scheduling", "seo_optimization", "ab_testing",
                     "custom_reports", "white_label", "priority_support",
                     "team_unlimited"},
    },
    "enterprise": {
        "price_cents": 0, "credits": 999, "name": "Enterprise",
        "cycle_price_cents": 300, "metered_rate_cents": 250,
        "annual_rollover_pct": 50,
        "features": {"basic_analytics", "advanced_analytics", "content_generation",
                     "social_scheduling", "seo_optimization", "ab_testing",
                     "custom_reports", "white_label", "priority_support",
                     "team_unlimited", "custom_domain", "sla_99_9",
                     "dedicated_support"},
    },
}

FEATURE_COMPLEXITY: dict[str, float] = {
    "content_generation": 1.0, "social_scheduling": 0.5, "seo_optimization": 1.5,
    "ab_testing": 2.0, "custom_reports": 1.2, "advanced_analytics": 1.8,
    "white_label": 3.0, "custom_domain": 2.5,
}

DISCOUNT_PROGRAMS: dict[str, dict[str, Any]] = {
    "startup": {"pct": 30, "max_months": 12,
                "eligible_plans": {"starter", "pro", "growth"}},
    "nonprofit": {"pct": 40, "max_months": 0,
                  "eligible_plans": {"starter", "pro", "growth", "scale"}},
    "education": {"pct": 50, "max_months": 12,
                  "eligible_plans": {"starter", "pro"}},
}

BUSINESS_SIZE_TIERS: list[dict[str, Any]] = [
    {"name": "solo", "max_employees": 1, "multiplier": 0.8},
    {"name": "micro", "max_employees": 10, "multiplier": 0.9},
    {"name": "small", "max_employees": 50, "multiplier": 1.0},
    {"name": "medium", "max_employees": 250, "multiplier": 1.15},
    {"name": "large", "max_employees": 1000, "multiplier": 1.3},
    {"name": "enterprise", "max_employees": 999999, "multiplier": 1.5},
]

PREPAID_PACKAGES: list[dict[str, Any]] = [
    {"id": "pack_10", "credits": 10, "price_cents": 9900, "bonus_credits": 1},
    {"id": "pack_25", "credits": 25, "price_cents": 22500, "bonus_credits": 3},
    {"id": "pack_50", "credits": 50, "price_cents": 42500, "bonus_credits": 8},
    {"id": "pack_100", "credits": 100, "price_cents": 79900, "bonus_credits": 20},
    {"id": "pack_250", "credits": 250, "price_cents": 187500, "bonus_credits": 60},
]

# Demand pricing band thresholds (cycles consumed system-wide in last hour)
DEMAND_BANDS: list[dict[str, Any]] = [
    {"min_demand": 0, "max_demand": 100, "multiplier": 1.0},
    {"min_demand": 101, "max_demand": 500, "multiplier": 1.1},
    {"min_demand": 501, "max_demand": 1000, "multiplier": 1.25},
    {"min_demand": 1001, "max_demand": 999999, "multiplier": 1.5},
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CreditAccount:
    business_id: int
    plan: str = "starter"
    billing_mode: str = "subscription"
    credits_remaining: int = 5
    credits_bonus: int = 0
    rollover_credits: int = 0
    metered_usage_cents: int = 0
    soft_limit_pct: float = 80.0
    hard_cap: int | None = None
    discount_program: str | None = None
    discount_expires_at: datetime | None = None
    annual_billing: bool = False
    employee_count: int = 1
    referral_code: str | None = None
    referred_by: str | None = None
    credits_gifted_out: int = 0
    credits_gifted_in: int = 0
    marketplace_listed_credits: int = 0
    spending_cap_cents: int | None = None
    total_spent_cents: int = 0
    credit_expiration_days: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CreditTransaction:
    business_id: int
    delta: int
    balance_after: int
    reason: str
    feature: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MarketplaceListing:
    id: str
    seller_business_id: int
    credits: int
    price_cents_per_credit: int
    status: str = "active"
    buyer_business_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=30))


@dataclass
class CreditExpiration:
    business_id: int
    credits: int
    expires_at: datetime
    notified_7d: bool = False
    notified_1d: bool = False
    expired: bool = False


@dataclass
class ROIEstimate:
    plan: str
    monthly_cost_cents: int
    estimated_hours_saved: float
    estimated_revenue_gain_cents: int
    roi_pct: float
    payback_months: float
    features_used: list[str] = field(default_factory=list)


@dataclass
class UsageWarning:
    business_id: int
    warning_type: str
    current_usage: int
    limit: int
    pct_used: float
    message: str


@dataclass
class AutoUpgradeSuggestion:
    business_id: int
    current_plan: str
    suggested_plan: str
    reasons: list[str]
    estimated_savings_cents: int
    confidence: float


# ---------------------------------------------------------------------------
# Billing engine
# ---------------------------------------------------------------------------

class MeteredBillingEngine:
    """Core billing engine -- credit metering, pay-per-cycle, spending caps."""

    def __init__(self) -> None:
        self._accounts: dict[int, CreditAccount] = {}
        self._transactions: list[CreditTransaction] = []
        self._expirations: list[CreditExpiration] = []
        self._system_demand: int = 0  # cycles in last hour

    # -- account management --

    def register_account(self, account: CreditAccount) -> CreditAccount:
        self._accounts[account.business_id] = account
        if not account.referral_code:
            raw = f"{account.business_id}-{account.created_at.isoformat()}"
            account.referral_code = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return account

    def get_account(self, business_id: int) -> CreditAccount | None:
        return self._accounts.get(business_id)

    # -- credit consumption --

    def consume_credit(
        self, business_id: int, amount: int = 1, feature: str | None = None,
    ) -> CreditTransaction:
        acct = self._require_account(business_id)
        # spending cap enforcement
        if acct.spending_cap_cents is not None:
            cost = self._credit_cost_cents(acct, amount)
            if acct.total_spent_cents + cost > acct.spending_cap_cents:
                raise ValueError("Spending cap exceeded")
        # hard cap enforcement
        if acct.hard_cap is not None and acct.credits_remaining - amount < 0:
            raise ValueError("Hard usage cap reached")
        # variable pricing by feature complexity
        effective_amount = self._feature_adjusted_amount(amount, feature)
        if acct.credits_remaining < effective_amount:
            raise ValueError(
                f"Insufficient credits: need {effective_amount}, have {acct.credits_remaining}")
        acct.credits_remaining -= effective_amount
        cost = self._credit_cost_cents(acct, effective_amount)
        acct.total_spent_cents += cost
        if acct.billing_mode == BillingMode.METERED.value:
            acct.metered_usage_cents += cost
        txn = CreditTransaction(
            business_id=business_id, delta=-effective_amount,
            balance_after=acct.credits_remaining, reason="consumption",
            feature=feature, metadata={"cost_cents": cost},
        )
        self._transactions.append(txn)
        return txn

    def pay_per_cycle(self, business_id: int) -> CreditTransaction:
        """Charge for a single cycle using pay-per-cycle pricing."""
        acct = self._require_account(business_id)
        plan_info = PLAN_CATALOG.get(acct.plan, PLAN_CATALOG["starter"])
        cost_cents = plan_info["cycle_price_cents"]
        # apply demand multiplier
        demand_mult = self._demand_multiplier()
        cost_cents = round(cost_cents * demand_mult)
        # apply discount
        cost_cents = self._apply_discount(acct, cost_cents)
        # apply size multiplier
        cost_cents = round(cost_cents * self._size_multiplier(acct.employee_count))
        if acct.spending_cap_cents is not None:
            if acct.total_spent_cents + cost_cents > acct.spending_cap_cents:
                raise ValueError("Spending cap exceeded")
        acct.total_spent_cents += cost_cents
        acct.metered_usage_cents += cost_cents
        txn = CreditTransaction(
            business_id=business_id, delta=0,
            balance_after=acct.credits_remaining, reason="pay_per_cycle",
            metadata={"cost_cents": cost_cents, "demand_multiplier": demand_mult},
        )
        self._transactions.append(txn)
        return txn

    # -- usage caps / warnings (259) --

    def check_usage_warnings(self, business_id: int) -> list[UsageWarning]:
        acct = self._require_account(business_id)
        plan_info = PLAN_CATALOG.get(acct.plan, PLAN_CATALOG["starter"])
        total = plan_info["credits"]
        if total <= 0:
            return []
        used = total - acct.credits_remaining
        pct = (used / total) * 100.0
        warnings: list[UsageWarning] = []
        if pct >= acct.soft_limit_pct:
            warnings.append(UsageWarning(
                business_id=business_id, warning_type="soft_limit",
                current_usage=used, limit=total, pct_used=pct,
                message=f"Usage at {pct:.0f}% of plan limit ({used}/{total} credits)",
            ))
        if pct >= 100.0:
            warnings.append(UsageWarning(
                business_id=business_id, warning_type="hard_limit",
                current_usage=used, limit=total, pct_used=pct,
                message=f"Credit limit reached ({used}/{total})",
            ))
        return warnings

    # -- dynamic pricing based on demand (262) --

    def set_system_demand(self, demand: int) -> None:
        self._system_demand = max(0, demand)

    def get_dynamic_price_cents(self, plan: str) -> int:
        plan_info = PLAN_CATALOG.get(plan, PLAN_CATALOG["starter"])
        base = plan_info["cycle_price_cents"]
        return round(base * self._demand_multiplier())

    # -- credit rollover for annual plans (265) --

    def process_rollover(self, business_id: int) -> int:
        acct = self._require_account(business_id)
        if not acct.annual_billing:
            return 0
        plan_info = PLAN_CATALOG.get(acct.plan, PLAN_CATALOG["starter"])
        rollover_pct = plan_info.get("annual_rollover_pct", 0)
        if rollover_pct <= 0:
            return 0
        rollover = min(
            acct.credits_remaining,
            round(plan_info["credits"] * rollover_pct / 100),
        )
        acct.rollover_credits = rollover
        # reset credits to plan base + rollover
        acct.credits_remaining = plan_info["credits"] + rollover
        txn = CreditTransaction(
            business_id=business_id, delta=rollover,
            balance_after=acct.credits_remaining, reason="rollover",
            metadata={"rollover_pct": rollover_pct},
        )
        self._transactions.append(txn)
        return rollover

    # -- credit gifting (268) --

    def gift_credits(
        self, from_business_id: int, to_business_id: int, amount: int,
    ) -> tuple[CreditTransaction, CreditTransaction]:
        if amount <= 0:
            raise ValueError("Gift amount must be positive")
        sender = self._require_account(from_business_id)
        receiver = self._require_account(to_business_id)
        if sender.credits_remaining < amount:
            raise ValueError("Insufficient credits for gift")
        sender.credits_remaining -= amount
        sender.credits_gifted_out += amount
        receiver.credits_remaining += amount
        receiver.credits_gifted_in += amount
        txn_send = CreditTransaction(
            business_id=from_business_id, delta=-amount,
            balance_after=sender.credits_remaining, reason="gift_sent",
            metadata={"to_business_id": to_business_id},
        )
        txn_recv = CreditTransaction(
            business_id=to_business_id, delta=amount,
            balance_after=receiver.credits_remaining, reason="gift_received",
            metadata={"from_business_id": from_business_id},
        )
        self._transactions.extend([txn_send, txn_recv])
        return txn_send, txn_recv

    # -- ROI calculator (274) --

    def calculate_roi(
        self, plan: str, hours_per_week_manual: float = 10.0,
        hourly_rate_cents: int = 5000, monthly_revenue_cents: int = 500000,
    ) -> ROIEstimate:
        plan_info = PLAN_CATALOG.get(plan, PLAN_CATALOG["starter"])
        monthly_cost = plan_info["price_cents"]
        hours_saved = hours_per_week_manual * 4 * 0.7  # 70% automation factor
        value_of_time = round(hours_saved * hourly_rate_cents)
        revenue_gain = round(monthly_revenue_cents * 0.05)  # 5% lift estimate
        total_benefit = value_of_time + revenue_gain
        roi_pct = ((total_benefit - monthly_cost) / max(monthly_cost, 1)) * 100
        payback = monthly_cost / max(total_benefit, 1)
        return ROIEstimate(
            plan=plan, monthly_cost_cents=monthly_cost,
            estimated_hours_saved=hours_saved,
            estimated_revenue_gain_cents=revenue_gain,
            roi_pct=round(roi_pct, 1),
            payback_months=round(payback, 2),
            features_used=sorted(plan_info["features"]),
        )

    # -- discount programs (277) --

    def apply_discount_program(
        self, business_id: int, program: str,
    ) -> dict[str, Any]:
        acct = self._require_account(business_id)
        prog = DISCOUNT_PROGRAMS.get(program)
        if not prog:
            raise ValueError(f"Unknown discount program: {program}")
        if acct.plan not in prog["eligible_plans"]:
            raise ValueError(
                f"Plan {acct.plan} not eligible for {program} discount")
        acct.discount_program = program
        if prog["max_months"] > 0:
            acct.discount_expires_at = (
                datetime.now(timezone.utc) + timedelta(days=30 * prog["max_months"])
            )
        else:
            acct.discount_expires_at = None  # perpetual
        return {
            "program": program, "discount_pct": prog["pct"],
            "expires_at": acct.discount_expires_at,
            "eligible_plans": sorted(prog["eligible_plans"]),
        }

    # -- credit marketplace (280) --

    def create_marketplace_listing(
        self, business_id: int, credits: int, price_cents_per_credit: int,
    ) -> MarketplaceListing:
        acct = self._require_account(business_id)
        if credits <= 0:
            raise ValueError("Must list at least 1 credit")
        available = acct.credits_remaining - acct.marketplace_listed_credits
        if credits > available:
            raise ValueError(
                f"Only {available} credits available for listing")
        listing_id = hashlib.sha256(
            f"{business_id}-{time.time()}".encode()
        ).hexdigest()[:16]
        listing = MarketplaceListing(
            id=listing_id, seller_business_id=business_id,
            credits=credits, price_cents_per_credit=price_cents_per_credit,
        )
        acct.marketplace_listed_credits += credits
        return listing

    def purchase_marketplace_listing(
        self, listing: MarketplaceListing, buyer_business_id: int,
    ) -> tuple[CreditTransaction, CreditTransaction]:
        if listing.status != "active":
            raise ValueError("Listing is not active")
        seller = self._require_account(listing.seller_business_id)
        buyer = self._require_account(buyer_business_id)
        total_cost = listing.credits * listing.price_cents_per_credit
        if buyer.spending_cap_cents is not None:
            if buyer.total_spent_cents + total_cost > buyer.spending_cap_cents:
                raise ValueError("Buyer spending cap exceeded")
        seller.credits_remaining -= listing.credits
        seller.marketplace_listed_credits -= listing.credits
        buyer.credits_remaining += listing.credits
        buyer.total_spent_cents += total_cost
        listing.status = "sold"
        listing.buyer_business_id = buyer_business_id
        txn_sell = CreditTransaction(
            business_id=listing.seller_business_id, delta=-listing.credits,
            balance_after=seller.credits_remaining, reason="marketplace_sold",
            metadata={"listing_id": listing.id, "revenue_cents": total_cost},
        )
        txn_buy = CreditTransaction(
            business_id=buyer_business_id, delta=listing.credits,
            balance_after=buyer.credits_remaining, reason="marketplace_purchased",
            metadata={"listing_id": listing.id, "cost_cents": total_cost},
        )
        self._transactions.extend([txn_sell, txn_buy])
        return txn_sell, txn_buy

    # -- auto-upgrade suggestions (283) --

    def suggest_upgrade(self, business_id: int) -> AutoUpgradeSuggestion | None:
        acct = self._require_account(business_id)
        plan_order = list(PLAN_CATALOG.keys())
        idx = plan_order.index(acct.plan) if acct.plan in plan_order else 0
        if idx >= len(plan_order) - 1:
            return None  # already top tier
        current_info = PLAN_CATALOG[acct.plan]
        usage_pct = 1.0 - (acct.credits_remaining / max(current_info["credits"], 1))
        reasons: list[str] = []
        confidence = 0.0
        if usage_pct >= 0.9:
            reasons.append("Consistently using >90% of plan credits")
            confidence += 0.4
        if usage_pct >= 0.7:
            reasons.append(f"Usage at {usage_pct*100:.0f}% of plan capacity")
            confidence += 0.2
        if acct.credits_gifted_in > 0:
            reasons.append("Receiving gifted credits suggests need for higher tier")
            confidence += 0.1
        if acct.metered_usage_cents > current_info["price_cents"] * 0.8:
            reasons.append("Metered spending approaching subscription cost")
            confidence += 0.3
        if not reasons:
            return None
        next_plan = plan_order[idx + 1]
        next_info = PLAN_CATALOG[next_plan]
        savings = max(0, acct.metered_usage_cents - next_info["price_cents"])
        return AutoUpgradeSuggestion(
            business_id=business_id, current_plan=acct.plan,
            suggested_plan=next_plan, reasons=reasons,
            estimated_savings_cents=savings,
            confidence=min(confidence, 1.0),
        )

    # -- referral bonuses (286) --

    def process_referral(
        self, referrer_business_id: int, new_business_id: int, bonus_credits: int = 5,
    ) -> tuple[CreditTransaction, CreditTransaction]:
        referrer = self._require_account(referrer_business_id)
        new_acct = self._require_account(new_business_id)
        referrer.credits_remaining += bonus_credits
        referrer.credits_bonus += bonus_credits
        new_acct.credits_remaining += bonus_credits
        new_acct.credits_bonus += bonus_credits
        new_acct.referred_by = referrer.referral_code
        txn_ref = CreditTransaction(
            business_id=referrer_business_id, delta=bonus_credits,
            balance_after=referrer.credits_remaining, reason="referral_bonus",
            metadata={"referred_business_id": new_business_id},
        )
        txn_new = CreditTransaction(
            business_id=new_business_id, delta=bonus_credits,
            balance_after=new_acct.credits_remaining, reason="referral_signup_bonus",
            metadata={"referrer_business_id": referrer_business_id},
        )
        self._transactions.extend([txn_ref, txn_new])
        return txn_ref, txn_new

    # -- credit expiration (444) --

    def set_credit_expiration(
        self, business_id: int, credits: int, days: int = 30,
    ) -> CreditExpiration:
        exp = CreditExpiration(
            business_id=business_id, credits=credits,
            expires_at=datetime.now(timezone.utc) + timedelta(days=days),
        )
        self._expirations.append(exp)
        return exp

    def check_expirations(self, now: datetime | None = None) -> list[CreditExpiration]:
        now = now or datetime.now(timezone.utc)
        results: list[CreditExpiration] = []
        for exp in self._expirations:
            if exp.expired:
                continue
            days_left = (exp.expires_at - now).total_seconds() / 86400
            if days_left <= 0:
                exp.expired = True
                acct = self._accounts.get(exp.business_id)
                if acct:
                    acct.credits_remaining = max(
                        0, acct.credits_remaining - exp.credits)
                results.append(exp)
            elif days_left <= 1 and not exp.notified_1d:
                exp.notified_1d = True
                results.append(exp)
            elif days_left <= 7 and not exp.notified_7d:
                exp.notified_7d = True
                results.append(exp)
        return results

    # -- spending caps for enterprise (447) --

    def set_spending_cap(self, business_id: int, cap_cents: int) -> None:
        acct = self._require_account(business_id)
        acct.spending_cap_cents = cap_cents

    def get_spending_remaining(self, business_id: int) -> int | None:
        acct = self._require_account(business_id)
        if acct.spending_cap_cents is None:
            return None
        return max(0, acct.spending_cap_cents - acct.total_spent_cents)

    # -- prepaid packages (451) --

    def purchase_prepaid_package(
        self, business_id: int, package_id: str,
    ) -> CreditTransaction:
        acct = self._require_account(business_id)
        package = None
        for p in PREPAID_PACKAGES:
            if p["id"] == package_id:
                package = p
                break
        if not package:
            raise ValueError(f"Unknown package: {package_id}")
        total_credits = package["credits"] + package["bonus_credits"]
        acct.credits_remaining += total_credits
        acct.credits_bonus += package["bonus_credits"]
        acct.total_spent_cents += package["price_cents"]
        txn = CreditTransaction(
            business_id=business_id, delta=total_credits,
            balance_after=acct.credits_remaining, reason="prepaid_purchase",
            metadata={
                "package_id": package_id,
                "base_credits": package["credits"],
                "bonus_credits": package["bonus_credits"],
                "price_cents": package["price_cents"],
            },
        )
        self._transactions.append(txn)
        return txn

    # -- credit purchase history & projections (460) --

    def get_transaction_history(
        self, business_id: int, limit: int = 50,
    ) -> list[CreditTransaction]:
        txns = [t for t in self._transactions if t.business_id == business_id]
        return txns[-limit:]

    def project_credit_usage(
        self, business_id: int, days_ahead: int = 30,
    ) -> dict[str, Any]:
        acct = self._require_account(business_id)
        txns = [t for t in self._transactions
                if t.business_id == business_id and t.delta < 0]
        if not txns:
            return {
                "current_credits": acct.credits_remaining,
                "projected_depletion_days": None,
                "avg_daily_usage": 0.0,
                "recommendation": "No usage data yet",
            }
        total_consumed = sum(abs(t.delta) for t in txns)
        # estimate days spanned
        if len(txns) > 1:
            span = (txns[-1].created_at - txns[0].created_at).total_seconds()
            span_days = max(span / 86400, 1)
        else:
            span_days = 1
        avg_daily = total_consumed / span_days
        if avg_daily <= 0:
            depletion = None
            rec = "Usage too low to project"
        else:
            depletion = acct.credits_remaining / avg_daily
            if depletion < 7:
                rec = "Credits running low -- consider upgrading or purchasing a pack"
            elif depletion < 14:
                rec = "Credits may run out within 2 weeks"
            else:
                rec = "Credit balance is healthy"
        return {
            "current_credits": acct.credits_remaining,
            "projected_depletion_days": round(depletion, 1) if depletion else None,
            "avg_daily_usage": round(avg_daily, 2),
            "recommendation": rec,
        }

    # -- usage reporting per feature (463) --

    def get_usage_by_feature(self, business_id: int) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for txn in self._transactions:
            if txn.business_id != business_id or txn.delta >= 0:
                continue
            feat = txn.feature or "general"
            if feat not in result:
                result[feat] = {"credits_used": 0, "transactions": 0, "cost_cents": 0}
            result[feat]["credits_used"] += abs(txn.delta)
            result[feat]["transactions"] += 1
            result[feat]["cost_cents"] += txn.metadata.get("cost_cents", 0)
        return result

    # -- pricing adjustment to prevent price churn (359) --

    def adjust_pricing_for_retention(
        self, business_id: int, months_subscribed: int,
    ) -> dict[str, Any]:
        acct = self._require_account(business_id)
        plan_info = PLAN_CATALOG.get(acct.plan, PLAN_CATALOG["starter"])
        base_price = plan_info["price_cents"]
        # loyalty discount: 2% per quarter, max 15%
        loyalty_pct = min(15, (months_subscribed // 3) * 2)
        adjusted = round(base_price * (1 - loyalty_pct / 100))
        return {
            "base_price_cents": base_price,
            "adjusted_price_cents": adjusted,
            "loyalty_discount_pct": loyalty_pct,
            "months_subscribed": months_subscribed,
        }

    # -- internal helpers --

    def _require_account(self, business_id: int) -> CreditAccount:
        acct = self._accounts.get(business_id)
        if not acct:
            raise ValueError(f"No account for business {business_id}")
        return acct

    def _credit_cost_cents(self, acct: CreditAccount, credits: int) -> int:
        plan_info = PLAN_CATALOG.get(acct.plan, PLAN_CATALOG["starter"])
        rate = plan_info["metered_rate_cents"]
        return credits * rate

    def _demand_multiplier(self) -> float:
        for band in DEMAND_BANDS:
            if band["min_demand"] <= self._system_demand <= band["max_demand"]:
                return band["multiplier"]
        return 1.0

    def _size_multiplier(self, employee_count: int) -> float:
        for tier in BUSINESS_SIZE_TIERS:
            if employee_count <= tier["max_employees"]:
                return tier["multiplier"]
        return 1.0

    def _feature_adjusted_amount(self, base: int, feature: str | None) -> int:
        if not feature or feature not in FEATURE_COMPLEXITY:
            return base
        return max(1, round(base * FEATURE_COMPLEXITY[feature]))

    def _apply_discount(self, acct: CreditAccount, price_cents: int) -> int:
        if not acct.discount_program:
            return price_cents
        prog = DISCOUNT_PROGRAMS.get(acct.discount_program)
        if not prog:
            return price_cents
        if acct.discount_expires_at and datetime.now(timezone.utc) > acct.discount_expires_at:
            acct.discount_program = None
            acct.discount_expires_at = None
            return price_cents
        return round(price_cents * (1 - prog["pct"] / 100))
