"""Analytics -- customer insights, segmentation, journey mapping, LTV,
referral analytics, onboarding funnels, A/B testing."""

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

__all__ = [
    "ABAssignment",
    "ABExperiment",
    "AnalyticsEngine",
    "CustomerInsight",
    "CustomerSegment",
    "ExperimentStatus",
    "JourneyEvent",
    "JourneyMap",
    "JourneyStage",
    "LTVPrediction",
    "OnboardingFunnel",
    "ReferralAnalytics",
    "SegmentType",
]
