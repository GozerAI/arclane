"""Commercial policy for preview access, subscriptions, and top-up working days."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanPolicy:
    key: str
    name: str
    price_cents: int
    working_days: int
    company_limit: int
    trial_days: int | None = None
    checkout_enabled: bool = True
    public: bool = True


@dataclass(frozen=True)
class DayPackPolicy:
    key: str
    name: str
    working_days: int
    price_cents: int


@dataclass(frozen=True)
class AddOnPolicy:
    key: str
    name: str
    included_cycles: int
    price_cents: int


PLAN_POLICIES: dict[str, PlanPolicy] = {
    "preview": PlanPolicy(
        key="preview",
        name="Preview",
        price_cents=0,
        working_days=3,
        company_limit=1,
        checkout_enabled=False,
        public=False,
    ),
    "starter": PlanPolicy(
        key="starter",
        name="Starter",
        price_cents=4900,
        working_days=10,
        company_limit=1,
        trial_days=3,
    ),
    "pro": PlanPolicy(
        key="pro",
        name="Pro",
        price_cents=9900,
        working_days=20,
        company_limit=1,
    ),
    "growth": PlanPolicy(
        key="growth",
        name="Growth",
        price_cents=24900,
        working_days=75,
        company_limit=3,
    ),
    "scale": PlanPolicy(
        key="scale",
        name="Scale",
        price_cents=49900,
        working_days=150,
        company_limit=5,
    ),
}

PUBLIC_PLANS = {
    key: policy
    for key, policy in PLAN_POLICIES.items()
    if policy.public
}

RECURRING_PLAN_WORKING_DAYS = {
    key: policy.working_days
    for key, policy in PUBLIC_PLANS.items()
}

PLAN_PRICES = {
    "preview": 0,
    "starter": 49,
    "pro": 99,
    "growth": 249,
    "scale": 499,
}

DAY_PACK_POLICIES: dict[str, DayPackPolicy] = {
    "boost-5": DayPackPolicy(
        key="boost-5",
        name="Boost 5",
        working_days=5,
        price_cents=14900,
    ),
    "boost-15": DayPackPolicy(
        key="boost-15",
        name="Boost 15",
        working_days=15,
        price_cents=39900,
    ),
}

ADD_ON_POLICIES: dict[str, AddOnPolicy] = {
    "deep-market-dive": AddOnPolicy(
        key="deep-market-dive",
        name="Deep market dive",
        included_cycles=3,
        price_cents=11900,
    ),
    "expanded-competitor-teardown": AddOnPolicy(
        key="expanded-competitor-teardown",
        name="Expanded competitor teardown",
        included_cycles=2,
        price_cents=7900,
    ),
    "landing-page-sprint": AddOnPolicy(
        key="landing-page-sprint",
        name="Landing page sprint",
        included_cycles=2,
        price_cents=8900,
    ),
    "social-batch-pack": AddOnPolicy(
        key="social-batch-pack",
        name="Social batch pack",
        included_cycles=2,
        price_cents=6900,
    ),
}

REVENUE_SHARE_PERCENT = 5.0
AD_SPEND_TAKE_PERCENT = 7.5
STRIPE_FEE_PERCENT = 2.9
STRIPE_FEE_FIXED_CENTS = 30


def get_plan_policy(plan: str | None) -> PlanPolicy:
    return PLAN_POLICIES.get(plan or "", PLAN_POLICIES["preview"])


def effective_day_value_cents(plan: str | None) -> int | None:
    policy = get_plan_policy(plan)
    if policy.price_cents <= 0 or policy.working_days <= 0:
        return None
    return round(policy.price_cents / policy.working_days)


def company_limit_for_account(plans: list[str]) -> int:
    if not plans:
        return PLAN_POLICIES["preview"].company_limit
    return max(get_plan_policy(plan).company_limit for plan in plans)
