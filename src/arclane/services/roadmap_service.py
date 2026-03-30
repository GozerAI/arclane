"""60-day incubator roadmap service — phase progression, milestones, task generation."""

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import (
    Business,
    Content,
    Milestone,
    RoadmapPhase,
)

log = get_logger("roadmap_service")

# Phase definitions
PHASES = {
    1: {"name": "Foundation", "days": (1, 15), "description": "Clear offer, validated positioning, live surface, first lead capture"},
    2: {"name": "Validation", "days": (16, 30), "description": "First customer signal, validated demand, distribution running"},
    3: {"name": "Growth", "days": (31, 45), "description": "Revenue signals, repeatable acquisition, scaling content"},
    4: {"name": "Scale-Ready", "days": (46, 60), "description": "Repeatable systems, investor readiness, graduation"},
}

# Map operating_plan core task keys → Phase 1 roadmap milestone keys
CORE_TASK_TO_MILESTONE = {
    "core-strategy-01": "p1-strategy-brief",
    "core-market-01": "p1-market-research",
    "core-content-01": "p1-landing-page-draft",
    "core-social-01": "p1-launch-tweet",
}

# Milestone definitions per phase — one per day.
# Day 1 deliverables (strategy, market research, landing page, tweet) are produced
# in the initial signup cycle so the user sees immediate value.
PHASE_MILESTONES = {
    1: [
        # Day 1 — initial cycle instant-value pack
        {"key": "p1-strategy-brief", "title": "Strategy brief completed", "category": "deliverable", "due_day": 1},
        {"key": "p1-market-research", "title": "Market research report completed", "category": "deliverable", "due_day": 1},
        {"key": "p1-landing-page-draft", "title": "Landing page draft created", "category": "deliverable", "due_day": 1},
        {"key": "p1-launch-tweet", "title": "Launch tweet published", "category": "deliverable", "due_day": 1},
        # Days 2–15 — one deliverable per day
        {"key": "p1-growth-asset", "title": "First growth asset published", "category": "deliverable", "due_day": 2},
        {"key": "p1-offer-refinement", "title": "Offer refined + 3 positioning statements", "category": "deliverable", "due_day": 3},
        {"key": "p1-icp-document", "title": "Ideal Customer Profile document created", "category": "deliverable", "due_day": 4},
        {"key": "p1-lead-capture", "title": "Lead capture mechanism designed", "category": "deliverable", "due_day": 5},
        {"key": "p1-founding-story", "title": "Founding story written (3 versions)", "category": "deliverable", "due_day": 6},
        {"key": "p1-content-social-batch", "title": "Social content batch: 3 posts", "category": "deliverable", "due_day": 7},
        {"key": "p1-competitor-01", "title": "Competitor profile #1", "category": "deliverable", "due_day": 8},
        {"key": "p1-competitor-02", "title": "Competitor profile #2", "category": "deliverable", "due_day": 9},
        {"key": "p1-competitor-03", "title": "Competitor profile #3 + positioning map", "category": "deliverable", "due_day": 10},
        {"key": "p1-seo-baseline", "title": "SEO baseline + keyword targets", "category": "deliverable", "due_day": 11},
        {"key": "p1-landing-v2-top", "title": "Landing page v2 — hero + problem + solution", "category": "deliverable", "due_day": 12},
        {"key": "p1-landing-v2-bottom", "title": "Landing page v2 — proof + FAQ + CTA + lead capture", "category": "deliverable", "due_day": 13},
        {"key": "p1-financial-model", "title": "Financial model + lead capture implementation guide", "category": "deliverable", "due_day": 14},
        {"key": "p1-graduation-check", "title": "SEO blog post + Phase 1 graduation report", "category": "gate", "due_day": 15},
    ],
    2: [
        # Days 16–30 — one deliverable per day
        {"key": "p2-validation-plan", "title": "Validation plan: 3 testable hypotheses", "category": "deliverable", "due_day": 16},
        {"key": "p2-outreach-cold", "title": "Cold outreach templates (email + LinkedIn)", "category": "deliverable", "due_day": 17},
        {"key": "p2-outreach-warm", "title": "Warm outreach + DM templates", "category": "deliverable", "due_day": 18},
        {"key": "p2-ad-brief", "title": "Paid acquisition brief ($200-500 test)", "category": "deliverable", "due_day": 19},
        {"key": "p2-content-calendar", "title": "30-day content calendar", "category": "deliverable", "due_day": 20},
        {"key": "p2-pricing-validation", "title": "Pricing validation analysis + pricing page copy", "category": "deliverable", "due_day": 21},
        {"key": "p2-seo-meta-pack", "title": "SEO meta copy pack (5 pages)", "category": "deliverable", "due_day": 22},
        {"key": "p2-blog-post-01", "title": "SEO blog post #1", "category": "deliverable", "due_day": 23},
        {"key": "p2-referral-program", "title": "Referral program design", "category": "deliverable", "due_day": 24},
        {"key": "p2-discovery-guide", "title": "Customer discovery interview guide + script", "category": "deliverable", "due_day": 25},
        {"key": "p2-content-batch", "title": "Content batch: 3 social posts + 1 email", "category": "deliverable", "due_day": 26},
        {"key": "p2-distribution-setup", "title": "Distribution channel setup guide", "category": "deliverable", "due_day": 27},
        {"key": "p2-objection-playbook", "title": "Objection handling playbook", "category": "deliverable", "due_day": 28},
        {"key": "p2-pitch-deck", "title": "Pitch deck (10 slides + speaker notes)", "category": "deliverable", "due_day": 29},
        {"key": "p2-graduation-check", "title": "Social proof system + Phase 2 graduation report", "category": "gate", "due_day": 30},
    ],
    3: [
        # Days 31–45 — one deliverable per day
        {"key": "p3-acq-playbook", "title": "Acquisition playbook — channel + targeting + scaling rules", "category": "deliverable", "due_day": 31},
        {"key": "p3-email-seq-12", "title": "Email nurture: emails 1-2 (welcome + value)", "category": "deliverable", "due_day": 32},
        {"key": "p3-email-seq-34", "title": "Email nurture: emails 3-4 (social proof + objection)", "category": "deliverable", "due_day": 33},
        {"key": "p3-email-seq-5", "title": "Email nurture: email 5 (CTA) + A/B recommendations", "category": "deliverable", "due_day": 34},
        {"key": "p3-blog-post-02", "title": "SEO blog post #2", "category": "deliverable", "due_day": 35},
        {"key": "p3-revenue-tracking", "title": "Revenue tracking: UTMs + events + attribution setup", "category": "deliverable", "due_day": 36},
        {"key": "p3-competitor-monitor", "title": "Competitive monitoring setup + 3 watch profiles", "category": "deliverable", "due_day": 37},
        {"key": "p3-content-batch", "title": "Content batch: 3 social posts + 1 email newsletter", "category": "deliverable", "due_day": 38},
        {"key": "p3-podcast-kit", "title": "Podcast pitch kit (pitch + bio + 3 topic proposals)", "category": "deliverable", "due_day": 39},
        {"key": "p3-ops-automation", "title": "Operations automation recommendations", "category": "deliverable", "due_day": 40},
        {"key": "p3-case-study", "title": "Case study template + first draft", "category": "deliverable", "due_day": 41},
        {"key": "p3-affiliate-blueprint", "title": "Affiliate program blueprint", "category": "deliverable", "due_day": 42},
        {"key": "p3-growth-experiment", "title": "Growth experiment design", "category": "deliverable", "due_day": 43},
        {"key": "p3-blog-post-03", "title": "SEO blog post #3 + brand voice document", "category": "deliverable", "due_day": 44},
        {"key": "p3-graduation-check", "title": "Growth experiment results + Phase 3 graduation report", "category": "gate", "due_day": 45},
    ],
    4: [
        # Days 46–60 — one deliverable per day
        {"key": "p4-scale-assessment", "title": "Scale assessment: what's working + what to cut", "category": "deliverable", "due_day": 46},
        {"key": "p4-press-kit", "title": "Press + media kit", "category": "deliverable", "due_day": 47},
        {"key": "p4-investor-brief", "title": "1-page investor brief", "category": "deliverable", "due_day": 48},
        {"key": "p4-hiring-plan", "title": "Hiring plan + first hire job description", "category": "deliverable", "due_day": 49},
        {"key": "p4-retention-onboarding", "title": "Retention playbook — onboarding flow", "category": "deliverable", "due_day": 50},
        {"key": "p4-retention-churn", "title": "Retention playbook — churn prevention + win-back", "category": "deliverable", "due_day": 51},
        {"key": "p4-investor-deck", "title": "Full investor deck (15 slides + speaker notes)", "category": "deliverable", "due_day": 52},
        {"key": "p4-content-final", "title": "Final content batch: 3 pieces", "category": "deliverable", "due_day": 53},
        {"key": "p4-exit-survey", "title": "Exit interview + churned user survey", "category": "deliverable", "due_day": 54},
        {"key": "p4-q2-plan-a", "title": "Q2 roadmap — priorities + channels + milestones", "category": "deliverable", "due_day": 55},
        {"key": "p4-q2-plan-b", "title": "Q2 roadmap — budget + risks + OKRs", "category": "deliverable", "due_day": 56},
        {"key": "p4-60day-report-a", "title": "60-day report — metrics + timeline + what we built", "category": "deliverable", "due_day": 57},
        {"key": "p4-60day-report-b", "title": "60-day report — learnings + what worked", "category": "deliverable", "due_day": 58},
        {"key": "p4-partner-ecosystem", "title": "Partner ecosystem map + integration opportunities", "category": "deliverable", "due_day": 59},
        {"key": "p4-graduation", "title": "Graduation assessment + Forever Partner welcome", "category": "gate", "due_day": 60},
    ],
}

# Phase task templates — one task per day for daily engagement.
# Day 1 tasks (strategy, market research, landing page, tweet) are handled by
# operating_plan.py in the initial cycle.  PHASE_TASKS covers Day 2 onward.
PHASE_TASKS = {
    1: {
        "day_2": [
            {"key": "p1-growth-asset", "area": "content", "action": "create_initial_content", "title": "First growth asset", "duration_days": 1,
             "milestone_key": "p1-growth-asset",
             "description": (
                 "Create one publishable growth asset for the first channel — a social post, launch post, or short email draft. "
                 "The strategy brief and market research from Day 1 are available. "
                 "Use the actual business name, offer, and wedge. Make it specific and ready to publish today."
             )},
        ],
        "day_3": [
            {"key": "p1-offer-refinement", "area": "strategy", "action": "refine_positioning", "title": "Offer refinement + positioning", "duration_days": 1,
             "milestone_key": "p1-offer-refinement",
             "description": (
                 "Sharpen the Day 1 strategy. Deliverables: (1) Rewrite the one-sentence offer so a stranger understands it in 5 seconds. "
                 "(2) Define the wedge — the one thing this business does that competitors don't say. "
                 "(3) Write 3 positioning statements: homepage, social bio, email header. "
                 "(4) List top 3 buyer objections with a one-line response to each. "
                 "Build on the strategy brief — do not repeat it."
             )},
        ],
        "day_4": [
            {"key": "p1-icp-document", "area": "strategy", "action": "create_icp", "title": "Ideal Customer Profile document", "duration_days": 1,
             "milestone_key": "p1-icp-document",
             "description": (
                 "Create a detailed ICP document. Deliverables: "
                 "(1) Primary buyer: job title, company type, team size, trigger event, top 3 pains, success metric, "
                 "top 3 objections, 5 real-language quotes from the market. "
                 "(2) Secondary buyer: same structure, shorter form. "
                 "(3) Anti-ICP: 3 customer types to avoid and why. "
                 "Use the strategy brief and market research as inputs."
             )},
        ],
        "day_5": [
            {"key": "p1-lead-capture", "area": "operations", "action": "design_lead_capture", "title": "Lead capture mechanism design", "duration_days": 1,
             "milestone_key": "p1-lead-capture",
             "description": (
                 "Design the lead capture system. Deliverables: "
                 "(1) Choose one mechanism: email opt-in, waitlist, free resource download, demo booking, or free trial — explain why. "
                 "(2) Write the opt-in copy: headline, 1-line description, CTA, confirmation message. "
                 "(3) Specify where it appears: landing page section, popup timing, or standalone page. "
                 "(4) Define the follow-up: what happens in the first 24 hours after signup? "
                 "Output should be ready to implement."
             )},
        ],
        "day_6": [
            {"key": "p1-founding-story", "area": "content", "action": "write_founding_story", "title": "Founding story (3 versions)", "duration_days": 1,
             "milestone_key": "p1-founding-story",
             "description": (
                 "Write the founding story in 3 versions: (1) 250-word version: the problem, the insight, and why this founder/team. "
                 "(2) 50-word version: optimized for bio pages and podcast intros. "
                 "(3) One-sentence version: for Twitter bios and deck header lines. "
                 "Tone: honest, specific, no cliches. Avoid 'disrupting' and 'passionate about'."
             )},
        ],
        "day_7": [
            {"key": "p1-content-social-batch", "area": "content", "action": "create_content_batch", "title": "Social content batch: 3 posts", "duration_days": 1,
             "milestone_key": "p1-content-social-batch",
             "description": (
                 "Create 3 social media posts using the refined positioning. "
                 "(1) Twitter/X: under 280 chars, strong hook. "
                 "(2) LinkedIn: 3-5 sentences, professional tone, insight from market research. "
                 "(3) Instagram: visual caption + 3 hashtags. "
                 "Use 3 different angles: pain point, solution, contrarian take. "
                 "Use the actual business name and offer — no placeholders."
             )},
        ],
        "day_8": [
            {"key": "p1-competitor-01", "area": "market_research", "action": "competitor_profiling", "title": "Competitor profile #1", "duration_days": 1,
             "milestone_key": "p1-competitor-01",
             "description": (
                 "Build a detailed profile for the #1 most relevant competitor: "
                 "(1) Company name and URL. (2) Core offer in one sentence. (3) Pricing model and price points. "
                 "(4) Messaging strengths. (5) Messaging weaknesses — where their copy is vague or proof is missing. "
                 "(6) One concrete differentiation opportunity this business can exploit. "
                 "Use the Day 1 market research as a starting point."
             )},
        ],
        "day_9": [
            {"key": "p1-competitor-02", "area": "market_research", "action": "competitor_profiling", "title": "Competitor profile #2", "duration_days": 1,
             "milestone_key": "p1-competitor-02",
             "description": (
                 "Build a detailed profile for the #2 competitor using the same structure: "
                 "name, URL, core offer, pricing, messaging strengths, weaknesses, and one differentiation opportunity. "
                 "Compare against competitor #1 — where do they overlap and where do they diverge?"
             )},
        ],
        "day_10": [
            {"key": "p1-competitor-03", "area": "market_research", "action": "competitor_profiling", "title": "Competitor profile #3 + positioning map", "duration_days": 1,
             "milestone_key": "p1-competitor-03",
             "description": (
                 "Build a detailed profile for the #3 competitor. Same structure as days 8-9. "
                 "Then add a competitive positioning matrix: "
                 "(1) Table comparing all 3 competitors + this business on 6 attributes (price, audience, channel, proof, speed to value, support). "
                 "(2) Positioning map narrative: 2 axes where this business wins. "
                 "(3) Two recommended positioning moves based on gaps in the competitor set."
             )},
        ],
        "day_11": [
            {"key": "p1-seo-baseline", "area": "market_research", "action": "seo_baseline", "title": "SEO baseline + keyword targets", "duration_days": 1,
             "milestone_key": "p1-seo-baseline",
             "description": (
                 "Establish the SEO baseline. Deliverables: "
                 "(1) 10-15 target keywords organized by intent (informational, commercial, transactional) "
                 "with estimated volume and competition level. "
                 "(2) On-page recommendations: title tag, meta description, H1, URL structure for homepage and landing page. "
                 "(3) 5 blog topic ideas mapped to target keywords that could drive organic traffic in 30-60 days. "
                 "(4) 3 quick-win changes that can be implemented this week."
             )},
        ],
        "day_12": [
            {"key": "p1-landing-v2-top", "area": "content", "action": "landing_page_v2", "title": "Landing page v2 — hero + problem + solution", "duration_days": 1,
             "milestone_key": "p1-landing-v2-top",
             "description": (
                 "Rewrite the top half of the landing page using everything learned in Days 1-11. Deliverables: "
                 "(1) Hero section: headline (outcome-focused, under 10 words), subheadline (1 sentence), CTA button text. "
                 "(2) Problem section: name the pain in the buyer's language using competitor profiles and market research. "
                 "(3) Solution section: how this business solves it, with 3 bullet proof points. "
                 "Write all copy — no wireframe labels."
             )},
        ],
        "day_13": [
            {"key": "p1-landing-v2-bottom", "area": "content", "action": "landing_page_v2", "title": "Landing page v2 — proof + FAQ + CTA + lead capture", "duration_days": 1,
             "milestone_key": "p1-landing-v2-bottom",
             "description": (
                 "Complete the landing page v2 — bottom half. Deliverables: "
                 "(1) Social proof section: testimonial framework or early traction metrics. "
                 "(2) FAQ: 8 entries covering what it is, pricing, comparison to alternatives, getting started, and trust. "
                 "(3) Objection handling: address the top 3 buyer objections. "
                 "(4) Lead capture section: integrate the Day 5 mechanism with actual copy. "
                 "(5) Final CTA. Write all copy."
             )},
        ],
        "day_14": [
            {"key": "p1-financial-model", "area": "finance", "action": "build_financial_model", "title": "Financial model + lead capture implementation guide", "duration_days": 1,
             "milestone_key": "p1-financial-model",
             "description": (
                 "Two deliverables: "
                 "(1) Unit economics model in table format: CAC across 2-3 channels, LTV over 12 months, gross margin, "
                 "LTV:CAC ratio, payback period, break-even customers, and one pricing recommendation. Label all assumptions. "
                 "(2) Lead capture implementation guide: step-by-step setup for the chosen mechanism, "
                 "finalized copy, integration points, and a test checklist."
             )},
        ],
        "day_15": [
            {"key": "p1-graduation-check", "area": "strategy", "action": "phase_graduation", "title": "SEO blog post + Phase 1 graduation report", "duration_days": 1,
             "milestone_key": "p1-graduation-check",
             "description": (
                 "Two deliverables: "
                 "(1) SEO blog post: 800-1200 words targeting a keyword from the Day 11 baseline, "
                 "with title, meta description, full body with headers. "
                 "(2) Phase 1 graduation report: summary of all 15 days of output, milestone completion checklist, "
                 "content count, 3 strengths to carry forward, 2 gaps Phase 2 should address, "
                 "and recommended focus for the first 3 days of Validation."
             )},
        ],
    },
    2: {
        "day_16": [
            {"key": "p2-validation-plan", "area": "strategy", "action": "create_validation_plan", "title": "Validation plan: 3 testable hypotheses", "duration_days": 1,
             "milestone_key": "p2-validation-plan",
             "description": (
                 "Phase 1 established the offer and positioning. Now validate demand. Deliverables: "
                 "(1) 3 testable hypotheses — one about who buys, one about where to find them, one about pricing. "
                 "(2) For each: the test, metric, threshold, and timeline (max 7 days). "
                 "(3) Decision matrix: if hypothesis X fails, here's Plan B. "
                 "Reference the ICP, competitor profiles, and pricing data from Phase 1."
             )},
        ],
        "day_17": [
            {"key": "p2-outreach-cold", "area": "content", "action": "create_outreach_templates", "title": "Cold outreach templates", "duration_days": 1,
             "milestone_key": "p2-outreach-cold",
             "description": (
                 "Write cold outreach templates: "
                 "(1) Cold email — subject line, 3-sentence body, CTA with 2 personalization variables. "
                 "(2) LinkedIn connection request — under 300 chars, references a shared pain point. "
                 "(3) LinkedIn follow-up after connection accepted. "
                 "Each template should reference the specific offer and wedge from the Phase 1 strategy brief."
             )},
        ],
        "day_18": [
            {"key": "p2-outreach-warm", "area": "content", "action": "create_outreach_templates", "title": "Warm outreach + DM templates", "duration_days": 1,
             "milestone_key": "p2-outreach-warm",
             "description": (
                 "Write warm outreach and DM templates: "
                 "(1) Warm intro email for when someone refers you — subject + body. "
                 "(2) Twitter/X DM — casual tone, under 160 chars. "
                 "(3) Follow-up email for warm leads who haven't responded in 5 days. "
                 "All templates should feel personal, not automated."
             )},
        ],
        "day_19": [
            {"key": "p2-ad-brief", "area": "operations", "action": "create_ad_brief", "title": "Paid acquisition brief", "duration_days": 1,
             "milestone_key": "p2-ad-brief",
             "description": (
                 "Create a paid acquisition plan for a $200-500 test budget. Deliverables: "
                 "(1) Recommended platform (Meta, Google, LinkedIn, Twitter/X) with rationale based on the ICP. "
                 "(2) Target audience definition: demographics, interests, behaviors, exclusions. "
                 "(3) 3 ad creative concepts (hook + body + CTA for each). "
                 "(4) Landing page recommendation and expected conversion rate. "
                 "(5) Success metrics and kill criteria for the test."
             )},
        ],
        "day_20": [
            {"key": "p2-content-calendar", "area": "content", "action": "create_content_calendar", "title": "30-day content calendar", "duration_days": 1,
             "milestone_key": "p2-content-calendar",
             "description": (
                 "Build a 30-day content calendar. Deliverables: "
                 "(1) 30 post topics mapped to days, channels, and format (blog, social, email, video). "
                 "(2) Themes: weeks 1-2 (awareness), weeks 3-4 (conversion). "
                 "(3) 5 pillar topics that each generate 3-5 derivative posts. "
                 "(4) Publishing schedule: which days, which channels, how often. "
                 "Use the ICP and SEO baseline as input for topic selection."
             )},
        ],
        "day_21": [
            {"key": "p2-pricing-validation", "area": "strategy", "action": "pricing_validation", "title": "Pricing validation + pricing page copy", "duration_days": 1,
             "milestone_key": "p2-pricing-validation",
             "description": (
                 "Two deliverables: "
                 "(1) Pricing validation analysis: compare current pricing against competitor prices, "
                 "ICP willingness to pay, and unit economics from Day 14. "
                 "Provide a recommendation: raise, lower, or restructure. "
                 "(2) Pricing page copy: headline, 2-3 plan names with descriptions, feature lists, "
                 "and a FAQ section (3 entries). Write all copy."
             )},
        ],
        "day_22": [
            {"key": "p2-seo-meta-pack", "area": "content", "action": "create_seo_meta_pack", "title": "SEO meta copy pack", "duration_days": 1,
             "milestone_key": "p2-seo-meta-pack",
             "description": (
                 "Create SEO meta copy for 5 important pages. "
                 "For each: title tag (<=60 chars), meta description (<=155 chars), H1, and OG/social preview title + description. "
                 "Pages: homepage, landing page, blog index, and 2 blog posts (use topics from the SEO baseline). "
                 "All copy should include target keywords naturally."
             )},
        ],
        "day_23": [
            {"key": "p2-blog-post-01", "area": "content", "action": "write_blog_post", "title": "SEO blog post #1", "duration_days": 1,
             "milestone_key": "p2-blog-post-01",
             "description": (
                 "Write one 800-1200 word blog post targeting a keyword from the Day 11 SEO baseline. "
                 "Include: title, meta description, full body with H2 headers, and a clear CTA at the end. "
                 "The post should address a specific question the target customer is searching for "
                 "and position the business as the expert source."
             )},
        ],
        "day_24": [
            {"key": "p2-referral-program", "area": "operations", "action": "design_referral_program", "title": "Referral program design", "duration_days": 1,
             "milestone_key": "p2-referral-program",
             "description": (
                 "Design the referral program. Deliverables: "
                 "(1) Incentive structure: what the referrer gets and what the referred person gets. "
                 "Justify the incentive amount relative to LTV. "
                 "(2) Referral mechanics: how the link/code works, where to share it, how it's tracked. "
                 "(3) Outreach copy: the email + social message existing customers get when invited to refer. "
                 "(4) Launch plan: when to activate and who to target first."
             )},
        ],
        "day_25": [
            {"key": "p2-discovery-guide", "area": "strategy", "action": "create_discovery_guide", "title": "Customer discovery interview guide + script", "duration_days": 1,
             "milestone_key": "p2-discovery-guide",
             "description": (
                 "Create a customer discovery guide. Deliverables: "
                 "(1) 15-question interview script covering: current workflow/problem, solution awareness, "
                 "willingness to pay, discovery channels, and buying process. "
                 "(2) Recruiting template: how to invite 5-10 target customers for a 20-minute call. "
                 "(3) Note-taking template for capturing insights. "
                 "(4) Analysis framework: how to synthesize responses into validated or invalidated hypotheses "
                 "from the validation plan."
             )},
        ],
        "day_26": [
            {"key": "p2-content-batch", "area": "content", "action": "create_content_batch", "title": "Content batch: 3 social + 1 email", "duration_days": 1,
             "milestone_key": "p2-content-batch",
             "description": (
                 "Create 4 content pieces: "
                 "(1) Twitter/X post — educational insight from competitor analysis or market research. "
                 "(2) LinkedIn post — behind-the-scenes or process story. "
                 "(3) Instagram/short-form — strong visual concept or quote. "
                 "(4) Email newsletter — subject line, preview text, 300-word body sharing one useful insight, and a CTA. "
                 "All content should use the 30-day calendar topics."
             )},
        ],
        "day_27": [
            {"key": "p2-distribution-setup", "area": "operations", "action": "setup_distribution", "title": "Distribution channel setup guide", "duration_days": 1,
             "milestone_key": "p2-distribution-setup",
             "description": (
                 "Create the distribution channel setup guide. Deliverables: "
                 "(1) Primary channel recommendation (organic social, email list, SEO, community, or paid) with rationale. "
                 "(2) Step-by-step setup instructions for the primary channel. "
                 "(3) 90-day distribution plan: what to publish, how often, target metrics. "
                 "(4) Secondary channel option with a lighter setup plan. "
                 "(5) Tracking setup: what metrics to monitor weekly."
             )},
        ],
        "day_28": [
            {"key": "p2-objection-playbook", "area": "strategy", "action": "create_objection_playbook", "title": "Objection handling playbook", "duration_days": 1,
             "milestone_key": "p2-objection-playbook",
             "description": (
                 "Build the objection handling playbook. Deliverables: "
                 "(1) Top 8 objections organized by category (price, timing, trust, competitor, complexity). "
                 "(2) For each: the response framework, example language, and follow-up question. "
                 "(3) Two versions per objection: written (for email/chat) and verbal (for calls). "
                 "(4) Escalation guide: when to discount, offer a trial, or walk away."
             )},
        ],
        "day_29": [
            {"key": "p2-pitch-deck", "area": "strategy", "action": "create_pitch_deck", "title": "Pitch deck (10 slides + notes)", "duration_days": 1,
             "milestone_key": "p2-pitch-deck",
             "description": (
                 "Write the 10-slide pitch deck. Each slide: title, 1-line subtitle, 3-5 bullet points of content, "
                 "and speaker notes (2-3 sentences). "
                 "Slides: (1) Problem, (2) Solution, (3) Market size, (4) Product demo/overview, "
                 "(5) Business model, (6) Traction/early signals, (7) Competitive landscape (use the matrix from Day 10), "
                 "(8) Go-to-market, (9) Team, (10) The ask. Write all content — no placeholder text."
             )},
        ],
        "day_30": [
            {"key": "p2-graduation-check", "area": "strategy", "action": "phase_graduation", "title": "Social proof system + Phase 2 graduation report", "duration_days": 1,
             "milestone_key": "p2-graduation-check",
             "description": (
                 "Two deliverables: "
                 "(1) Social proof collection system: templates for requesting testimonials (email + LinkedIn DM), "
                 "a review request sequence, and a framework for displaying proof on the landing page. "
                 "(2) Phase 2 graduation report: summary of validation work done, which hypotheses were confirmed or rejected, "
                 "distribution channel status, content count, 3 wins to build on, 2 gaps Phase 3 should address."
             )},
        ],
    },
    3: {
        "day_31": [
            {"key": "p3-acq-playbook", "area": "strategy", "action": "create_acquisition_playbook", "title": "Acquisition playbook", "duration_days": 1,
             "milestone_key": "p3-acq-playbook",
             "description": (
                 "Build the full acquisition playbook. Deliverables: "
                 "(1) Primary channel breakdown: audience targeting, content strategy, budget allocation, and expected CAC. "
                 "(2) Secondary channel plan. "
                 "(3) Scaling rules: at what metrics do you increase spend? "
                 "(4) Attribution setup: how to track which channel drives which customer. "
                 "(5) Weekly operating cadence: what to review, what to act on."
             )},
        ],
        "day_32": [
            {"key": "p3-email-seq-12", "area": "content", "action": "write_email_sequence", "title": "Email nurture: emails 1-2", "duration_days": 1,
             "milestone_key": "p3-email-seq-12",
             "description": (
                 "Write the first 2 emails of the 5-email nurture sequence. "
                 "Email 1 (Welcome — sent immediately): subject, preview text, 200-word body introducing the business, "
                 "one quick win for the subscriber, and a soft next step. "
                 "Email 2 (Day 3 — Value): subject, preview text, 250-word body sharing one actionable insight the ICP cares about, no hard sell. "
                 "Each email needs a distinct subject line that stands alone."
             )},
        ],
        "day_33": [
            {"key": "p3-email-seq-34", "area": "content", "action": "write_email_sequence", "title": "Email nurture: emails 3-4", "duration_days": 1,
             "milestone_key": "p3-email-seq-34",
             "description": (
                 "Write emails 3-4 of the nurture sequence. "
                 "Email 3 (Day 7 — Social Proof): subject, preview text, a case study or early traction story, and a soft CTA (book a call or try it). "
                 "Email 4 (Day 10 — Objection): subject, preview text, address the #1 objection head-on, "
                 "acknowledge doubt, provide evidence, reframe the risk. "
                 "These should feel like a conversation, not a funnel."
             )},
        ],
        "day_34": [
            {"key": "p3-email-seq-5", "area": "content", "action": "write_email_sequence", "title": "Email nurture: email 5 + A/B recs", "duration_days": 1,
             "milestone_key": "p3-email-seq-5",
             "description": (
                 "Two deliverables: "
                 "(1) Email 5 (Day 14 — Final CTA): subject, preview text, 150-word body with urgency framing, "
                 "direct ask, and social proof element. Make the ask specific and time-bound. "
                 "(2) A/B test recommendations: for each of the 5 emails, suggest one subject line variant to test "
                 "and one body variant to test. Include the hypothesis for each test."
             )},
        ],
        "day_35": [
            {"key": "p3-blog-post-02", "area": "content", "action": "write_blog_post", "title": "SEO blog post #2", "duration_days": 1,
             "milestone_key": "p3-blog-post-02",
             "description": (
                 "Write one 1000-1500 word blog post targeting a keyword from the SEO baseline. "
                 "This post should be higher intent than blog post #1 — target a comparison or best of keyword "
                 "that buyers search before purchasing. "
                 "Include: title, meta description, full body with headers, comparison table if relevant, "
                 "and a CTA to start a trial or book a call."
             )},
        ],
        "day_36": [
            {"key": "p3-revenue-tracking", "area": "operations", "action": "setup_revenue_tracking", "title": "Revenue tracking setup", "duration_days": 1,
             "milestone_key": "p3-revenue-tracking",
             "description": (
                 "Build the revenue tracking foundation. Deliverables: "
                 "(1) UTM framework: naming convention for all campaigns, channels, and content pieces. "
                 "(2) 5 key events to track (page view, signup, activation, purchase, churn). "
                 "(3) Attribution model recommendation (first-touch, last-touch, or multi-touch) with rationale. "
                 "(4) Dashboard spec: 6 metrics to display with definition and target value for each. "
                 "(5) Reporting cadence: what to review daily, weekly, monthly."
             )},
        ],
        "day_37": [
            {"key": "p3-competitor-monitor", "area": "market_research", "action": "setup_competitive_monitoring", "title": "Competitive monitoring setup", "duration_days": 1,
             "milestone_key": "p3-competitor-monitor",
             "description": (
                 "Set up competitive monitoring. Deliverables: "
                 "(1) Watch profile for each of the 3 competitors from Phase 1: "
                 "what to track (pricing changes, new features, messaging shifts, job postings). "
                 "(2) Monitoring cadence: weekly checks for pricing/messaging, monthly for strategy. "
                 "(3) Alert criteria: what changes warrant an immediate response. "
                 "(4) Competitive response playbook: if competitor drops price, launches feature X, "
                 "or targets our ICP directly — here's our move."
             )},
        ],
        "day_38": [
            {"key": "p3-content-batch", "area": "content", "action": "create_content_batch", "title": "Content batch: 3 social + 1 email", "duration_days": 1,
             "milestone_key": "p3-content-batch",
             "description": (
                 "Create 4 content pieces from the 30-day content calendar: "
                 "(1) Twitter/X thread (3-5 tweets, educational angle from acquisition insights). "
                 "(2) LinkedIn post (specific insight from the acquisition playbook or revenue tracking). "
                 "(3) Short-form post for any platform. "
                 "(4) Email newsletter — subject, preview, 300-word body on a topic from the calendar, CTA. "
                 "This batch pushes total content count higher."
             )},
        ],
        "day_39": [
            {"key": "p3-podcast-kit", "area": "content", "action": "create_podcast_kit", "title": "Podcast pitch kit", "duration_days": 1,
             "milestone_key": "p3-podcast-kit",
             "description": (
                 "Create the podcast pitch kit. Deliverables: "
                 "(1) Guest pitch letter: 200 words, subject line, why this guest fits this audience, "
                 "3 episode topic ideas with 1-line descriptions, bio (50 words), and social proof. "
                 "(2) Full bio (150 words) for show notes. "
                 "(3) List of 10 targeted podcasts in the niche with host name, audience size estimate, "
                 "and episode topic recommendation for each. "
                 "(4) Follow-up template for non-responses after 7 days."
             )},
        ],
        "day_40": [
            {"key": "p3-ops-automation", "area": "operations", "action": "operations_automation", "title": "Operations automation recommendations", "duration_days": 1,
             "milestone_key": "p3-ops-automation",
             "description": (
                 "Identify and document operations automation opportunities. Deliverables: "
                 "(1) Current workflow audit: 5-7 manual processes that are repeated weekly. "
                 "(2) Automation recommendation for each: tool, setup time estimate, and expected hours saved per month. "
                 "(3) Priority ranking: which 3 automations to implement first and why. "
                 "(4) Implementation guide for the #1 priority automation: step-by-step with screenshots/steps."
             )},
        ],
        "day_41": [
            {"key": "p3-case-study", "area": "content", "action": "write_case_study", "title": "Case study template + first draft", "duration_days": 1,
             "milestone_key": "p3-case-study",
             "description": (
                 "Two deliverables: "
                 "(1) Case study template: structure for capturing customer success stories — situation, challenge, solution, results, quote. "
                 "(2) First case study draft: if a real customer exists, write a full case study (400-600 words). "
                 "If not, write a composite case study based on the ICP using realistic assumptions — clearly label it as illustrative. "
                 "Include: headline, challenge, how they found us, solution details, results with metrics, and a pull quote."
             )},
        ],
        "day_42": [
            {"key": "p3-affiliate-blueprint", "area": "operations", "action": "create_affiliate_blueprint", "title": "Affiliate program blueprint", "duration_days": 1,
             "milestone_key": "p3-affiliate-blueprint",
             "description": (
                 "Design the affiliate program. Deliverables: "
                 "(1) Commission structure: percentage or flat fee, tier structure if applicable, cookie duration. "
                 "Justify the numbers relative to LTV and CAC. "
                 "(2) Affiliate profile: who makes an ideal affiliate (audience type, platform, content format). "
                 "(3) Recruitment outreach: email template for recruiting affiliates. "
                 "(4) Onboarding kit: what materials affiliates get (copy, images, tracking links). "
                 "(5) Performance tiers: what affiliates get at 5, 10, 25+ referrals."
             )},
        ],
        "day_43": [
            {"key": "p3-growth-experiment", "area": "strategy", "action": "design_growth_experiment", "title": "Growth experiment design", "duration_days": 1,
             "milestone_key": "p3-growth-experiment",
             "description": (
                 "Design one high-impact growth experiment. Deliverables: "
                 "(1) Experiment name and hypothesis: If we do X, then Y will happen, because Z. "
                 "(2) Test design: control vs. variant, sample size, duration. "
                 "(3) Success metric and threshold — what constitutes a win. "
                 "(4) Implementation plan: what exactly to build or change, estimated effort. "
                 "(5) Kill criteria: what results mean we stop immediately. "
                 "(6) Rollout plan: how to scale if it wins."
             )},
        ],
        "day_44": [
            {"key": "p3-blog-post-03", "area": "content", "action": "write_blog_post", "title": "SEO blog post #3 + brand voice guide", "duration_days": 1,
             "milestone_key": "p3-blog-post-03",
             "description": (
                 "Two deliverables: "
                 "(1) SEO blog post #3: 800-1200 words on a topic that drives top-of-funnel traffic. "
                 "Target an informational keyword. Include title, meta description, full body, "
                 "and internal links to the other 2 blog posts. "
                 "(2) Brand voice guide (short form): 4 brand voice attributes (e.g., Direct, not harsh), "
                 "2 example sentences for each showing on-brand vs. off-brand, "
                 "and a channel-specific tone note for Twitter, LinkedIn, and email."
             )},
        ],
        "day_45": [
            {"key": "p3-graduation-check", "area": "strategy", "action": "phase_graduation", "title": "Growth experiment results + Phase 3 graduation report", "duration_days": 1,
             "milestone_key": "p3-graduation-check",
             "description": (
                 "Two deliverables: "
                 "(1) Growth experiment results brief: what was tested, what happened, key metrics, interpretation, "
                 "and recommended next action. If not enough data yet, document the test status and extrapolate. "
                 "(2) Phase 3 graduation report: summary of all growth systems built, acquisition playbook status, "
                 "email sequence performance, content count, revenue tracking status, "
                 "3 wins to carry into Phase 4, and 2 gaps to address."
             )},
        ],
    },
    4: {
        "day_46": [
            {"key": "p4-scale-assessment", "area": "strategy", "action": "scale_assessment", "title": "Scale assessment", "duration_days": 1,
             "milestone_key": "p4-scale-assessment",
             "description": (
                 "Run the scale assessment. Deliverables: "
                 "(1) What's working: top 3 channels, campaigns, and content types by performance. "
                 "(2) What to cut: 3 things consuming resources without results. "
                 "(3) Bottlenecks: the single biggest constraint on growth right now. "
                 "(4) Scale levers: for each working channel, what would 2x investment produce? "
                 "(5) Recommended allocation shift: how to redeploy the resources freed from cutting."
             )},
        ],
        "day_47": [
            {"key": "p4-press-kit", "area": "content", "action": "create_press_kit", "title": "Press + media kit", "duration_days": 1,
             "milestone_key": "p4-press-kit",
             "description": (
                 "Create the press and media kit. Deliverables: "
                 "(1) Company boilerplate: 50-word and 100-word versions. "
                 "(2) Founder bio: 100-word version. "
                 "(3) Key stats and traction numbers (use real data or projected milestones). "
                 "(4) 3 press angles: story ideas a journalist or podcaster could pitch. "
                 "(5) Product/logo asset descriptions (describe what images to include). "
                 "(6) Media contact info template. Format as a downloadable document."
             )},
        ],
        "day_48": [
            {"key": "p4-investor-brief", "area": "strategy", "action": "create_investor_brief", "title": "1-page investor brief", "duration_days": 1,
             "milestone_key": "p4-investor-brief",
             "description": (
                 "Write the 1-page investor brief. Sections: "
                 "(1) Company name and tagline. (2) Problem (2 sentences). (3) Solution (2 sentences). "
                 "(4) Market size (TAM/SAM/SOM). (5) Traction (best metrics available). "
                 "(6) Business model (how money is made). (7) Team (who + why them). "
                 "(8) The ask (amount, use of funds, timeline). "
                 "Keep each section to 2-3 lines. Total length: fits on one A4 page."
             )},
        ],
        "day_49": [
            {"key": "p4-hiring-plan", "area": "operations", "action": "create_hiring_plan", "title": "Hiring plan + job description", "duration_days": 1,
             "milestone_key": "p4-hiring-plan",
             "description": (
                 "Create the first hire plan. Deliverables: "
                 "(1) Role analysis: what's the biggest time/skill constraint on growth right now? "
                 "(2) First hire profile: title, 5 core responsibilities, 3 must-have skills, 2 nice-to-have skills, culture traits. "
                 "(3) Full job description: role overview, responsibilities, requirements, compensation range, and how to apply. "
                 "(4) Interview process: 3-step process with the key question for each stage. "
                 "(5) Candidate sourcing plan: where to post, who to ask for referrals."
             )},
        ],
        "day_50": [
            {"key": "p4-retention-onboarding", "area": "operations", "action": "create_retention_playbook", "title": "Retention playbook — onboarding", "duration_days": 1,
             "milestone_key": "p4-retention-onboarding",
             "description": (
                 "Build the onboarding section of the retention playbook. Deliverables: "
                 "(1) Day 0 experience: what the customer gets immediately after signing up "
                 "(welcome email, first action, support contact). "
                 "(2) Days 1-7 activation sequence: 3 emails or in-app nudges that drive the customer to their first win. "
                 "(3) Day 30 check-in: how to proactively reach out, what to ask, and how to identify at-risk customers. "
                 "(4) Success criteria: what does a successfully onboarded customer look like? Metrics to track."
             )},
        ],
        "day_51": [
            {"key": "p4-retention-churn", "area": "operations", "action": "create_retention_playbook", "title": "Retention playbook — churn prevention + win-back", "duration_days": 1,
             "milestone_key": "p4-retention-churn",
             "description": (
                 "Build the churn prevention and win-back sections. Deliverables: "
                 "(1) Churn signals: 5 leading indicators that a customer is about to cancel. "
                 "(2) Intervention playbook: for each signal, what to do (email, call, offer, pause option). "
                 "(3) Cancellation flow: what happens when a customer cancels — what to offer, what to ask, what to save. "
                 "(4) Win-back sequence: 3-email campaign targeting churned customers at 30, 60, and 90 days post-cancel."
             )},
        ],
        "day_52": [
            {"key": "p4-investor-deck", "area": "strategy", "action": "create_investor_deck", "title": "Full investor deck (15 slides)", "duration_days": 1,
             "milestone_key": "p4-investor-deck",
             "description": (
                 "Write the full 15-slide investor deck. Each slide: title, 1-line subtitle, 3-5 bullet content points, "
                 "and speaker notes (2-3 sentences). "
                 "Slides: (1) Title + tagline, (2) The problem, (3) The solution, (4) Market opportunity, "
                 "(5) Product overview, (6) Business model, (7) Traction and metrics, (8) Competitive landscape, "
                 "(9) Go-to-market, (10) Unit economics and LTV:CAC, (11) Team, (12) Roadmap, "
                 "(13) Financial projections (3 years), (14) Risk + mitigation, (15) The ask. Write all content."
             )},
        ],
        "day_53": [
            {"key": "p4-content-final", "area": "content", "action": "create_content_batch", "title": "Final content batch: 3 pieces", "duration_days": 1,
             "milestone_key": "p4-content-final",
             "description": (
                 "Create 3 final content pieces that summarize the business's 60-day journey and expertise: "
                 "(1) Twitter/X thread: 60 days of building [Business Name] — what we learned. "
                 "(2) LinkedIn post: a lessons-learned narrative from the incubator, targeting other founders. "
                 "(3) Email newsletter: milestone update to the list — what's been built, where the business is heading. "
                 "All 3 should feel proud, authentic, and directional."
             )},
        ],
        "day_54": [
            {"key": "p4-exit-survey", "area": "operations", "action": "create_exit_survey", "title": "Exit interview + churned user survey", "duration_days": 1,
             "milestone_key": "p4-exit-survey",
             "description": (
                 "Create the exit intelligence tools. Deliverables: "
                 "(1) Churned customer survey: 8 questions covering why they left, what almost made them stay, "
                 "what they'll use instead, and one thing to fix. "
                 "(2) Exit interview script: 10 questions for a 15-minute call with a churned user, "
                 "including probing follow-ups. "
                 "(3) Analysis template: how to score and categorize exit responses into actionable themes. "
                 "(4) Internal review process: who sees this data, how often, and what triggers a product or pricing change."
             )},
        ],
        "day_55": [
            {"key": "p4-q2-plan-a", "area": "strategy", "action": "create_q2_roadmap", "title": "Q2 roadmap — priorities + channels + milestones", "duration_days": 1,
             "milestone_key": "p4-q2-plan-a",
             "description": (
                 "Build the first half of the Q2 roadmap. Deliverables: "
                 "(1) Top 3 strategic priorities for Q2 with rationale. "
                 "(2) Channel plan: which acquisition channels to invest in, at what level. "
                 "(3) Product/offer roadmap: top 3 improvements or additions planned for Q2. "
                 "(4) Milestones: 3 measurable goals to hit by end of Q2 (revenue, users, content, partnerships). "
                 "(5) Key assumptions: what must be true for this plan to work."
             )},
        ],
        "day_56": [
            {"key": "p4-q2-plan-b", "area": "strategy", "action": "create_q2_roadmap", "title": "Q2 roadmap — budget + risks + OKRs", "duration_days": 1,
             "milestone_key": "p4-q2-plan-b",
             "description": (
                 "Complete the Q2 roadmap. Deliverables: "
                 "(1) Budget allocation: how much to spend across channels, tools, and team in Q2. "
                 "(2) Top 3 risks and mitigation strategies. "
                 "(3) OKRs: 2-3 Objectives with 2-3 Key Results each. "
                 "(4) Dependencies: what decisions or external events could block progress. "
                 "(5) 90-day check-in plan: how to review and adjust the Q2 plan mid-quarter."
             )},
        ],
        "day_57": [
            {"key": "p4-60day-report-a", "area": "strategy", "action": "create_60day_report", "title": "60-day report — metrics + what we built", "duration_days": 1,
             "milestone_key": "p4-60day-report-a",
             "description": (
                 "First half of the 60-day report. Deliverables: "
                 "(1) Executive summary (3-5 sentences): what this business is and where it stands after 60 days. "
                 "(2) Timeline: a day-by-day log of all major deliverables produced. "
                 "(3) Key metrics: content count, email list size (estimate or target), SEO baseline status, "
                 "lead capture conversions, revenue or revenue pipeline. "
                 "(4) What was built: complete inventory of all assets created in the 60-day program."
             )},
        ],
        "day_58": [
            {"key": "p4-60day-report-b", "area": "strategy", "action": "create_60day_report", "title": "60-day report — learnings + what worked", "duration_days": 1,
             "milestone_key": "p4-60day-report-b",
             "description": (
                 "Second half of the 60-day report. Deliverables: "
                 "(1) What worked: top 5 things that had the most impact. "
                 "(2) What didn't work: 3 things tried that produced no results and the lesson from each. "
                 "(3) Biggest insight about the market, customer, or business model. "
                 "(4) Benchmark: where this business stands vs. typical 60-day startups (use realistic comparisons). "
                 "(5) Recommended priorities for months 3-6 based on everything learned."
             )},
        ],
        "day_59": [
            {"key": "p4-partner-ecosystem", "area": "strategy", "action": "map_partner_ecosystem", "title": "Partner ecosystem map + integration opportunities", "duration_days": 1,
             "milestone_key": "p4-partner-ecosystem",
             "description": (
                 "Map the partner ecosystem. Deliverables: "
                 "(1) Partner category map: 4-5 categories of partners "
                 "(tech integrations, distribution partners, referral partners, strategic alliances, white-label). "
                 "(2) Top 10 specific partner targets with name, URL, audience overlap, and partnership angle. "
                 "(3) Outreach template for each category. "
                 "(4) Partnership value proposition: what this business offers a partner vs. what it needs. "
                 "(5) First 90 days partner plan: which 3 to pursue first and the goal for each."
             )},
        ],
        "day_60": [
            {"key": "p4-graduation", "area": "strategy", "action": "phase_graduation", "title": "Graduation assessment + Forever Partner welcome", "duration_days": 1,
             "milestone_key": "p4-graduation",
             "description": (
                 "The final program deliverable. "
                 "(1) Graduation score (0-100): assess the business across 5 dimensions — "
                 "positioning (0-20), content engine (0-20), acquisition system (0-20), "
                 "revenue readiness (0-20), and operational foundation (0-20). "
                 "Provide the score, rationale, and top recommendation per dimension. "
                 "(2) Forever Partner welcome: what changes now that the structured program is complete — "
                 "the nightly AI will shift to adaptive optimization mode, here's what that means for the next 30 days. "
                 "(3) Top 3 priorities for day 61+."
             )},
        ],
    },
}


def get_phase_for_day(day: int) -> int:
    """Return the phase number (1-4) for a given roadmap day, or 5 for post-graduation."""
    for phase_num, info in PHASES.items():
        start, end = info["days"]
        if start <= day <= end:
            return phase_num
    if day > 60:
        return 5  # Post-graduation / forever partner
    return 0


def get_phase_tasks(phase: int, week_group: str | None = None) -> list[dict]:
    """Return task templates for a given phase and optional week group."""
    phase_tasks = PHASE_TASKS.get(phase, {})
    if week_group:
        return phase_tasks.get(week_group, [])
    all_tasks = []
    for tasks in phase_tasks.values():
        all_tasks.extend(tasks)
    return all_tasks


async def initialize_roadmap(business: Business, session: AsyncSession) -> None:
    """Initialize the 90-day roadmap for a newly created business."""
    # Create all 4 phase records
    for phase_num, info in PHASES.items():
        phase = RoadmapPhase(
            business_id=business.id,
            phase_number=phase_num,
            phase_name=info["name"],
            status="active" if phase_num == 1 else "locked",
            graduation_criteria=_graduation_criteria(phase_num),
            started_at=datetime.now(timezone.utc) if phase_num == 1 else None,
        )
        session.add(phase)

    # Create milestones for all phases
    for phase_num, milestones in PHASE_MILESTONES.items():
        for m in milestones:
            session.add(Milestone(
                business_id=business.id,
                phase_number=phase_num,
                key=m["key"],
                title=m["title"],
                category=m["category"],
                target_value=m.get("target_value"),
                due_day=m.get("due_day"),
            ))

    # Set business roadmap state
    business.roadmap_day = 1
    business.current_phase = 1

    await session.flush()
    log.info("Roadmap initialized for %s with 4 phases", business.slug)


def _graduation_criteria(phase: int) -> dict:
    """Return graduation criteria for a phase."""
    criteria = {
        1: {
            "required_milestones": [
                "p1-strategy-brief", "p1-market-research", "p1-landing-page-draft",
                "p1-icp-document", "p1-lead-capture", "p1-financial-model", "p1-graduation-check",
            ],
            "content_minimum": 5,
            "description": "Day 1 pack + ICP + lead capture + financial model + Phase 1 report done, 5+ content pieces",
        },
        2: {
            "required_milestones": [
                "p2-validation-plan", "p2-content-calendar", "p2-distribution-setup", "p2-pricing-validation", "p2-graduation-check",
            ],
            "content_minimum": 10,
            "description": "Validation plan, content calendar, distribution setup, pricing analysis, and Phase 2 report done, 10+ content pieces",
        },
        3: {
            "required_milestones": [
                "p3-acq-playbook", "p3-email-seq-5", "p3-revenue-tracking", "p3-growth-experiment", "p3-graduation-check",
            ],
            "content_minimum": 18,
            "description": "Acquisition playbook, email sequence, revenue tracking, growth experiment, and Phase 3 report done, 18+ content pieces",
        },
        4: {
            "required_milestones": ["p4-60day-report-b", "p4-q2-plan-b", "p4-graduation"],
            "content_minimum": 22,
            "graduation_score_minimum": 60,
            "description": "60-day report, Q2 roadmap, and graduation assessment done, 22+ content pieces",
        },
    }
    return criteria.get(phase, {})


async def check_phase_graduation(business: Business, session: AsyncSession) -> dict:
    """Check whether the business's current phase graduation criteria are met.

    Returns dict with 'ready' (bool), 'score' (float 0-100), 'met' (list), 'unmet' (list).
    """
    phase_num = business.current_phase
    if phase_num < 1 or phase_num > 4:
        return {"ready": False, "score": 0, "met": [], "unmet": ["Invalid phase"]}

    criteria = _graduation_criteria(phase_num)
    required = criteria.get("required_milestones", [])
    content_min = criteria.get("content_minimum", 0)

    # Check milestone completion
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.phase_number == phase_num,
        )
    )
    milestones = result.scalars().all()
    completed_keys = {m.key for m in milestones if m.status == "completed"}

    met = []
    unmet = []

    for req_key in required:
        if req_key in completed_keys:
            met.append(req_key)
        else:
            unmet.append(req_key)

    # Check content count
    content_count = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0

    if content_count >= content_min:
        met.append(f"content_count>={content_min}")
    else:
        unmet.append(f"content_count>={content_min} (current: {content_count})")

    # Calculate score
    total_criteria = len(required) + 1  # +1 for content count
    score = (len([r for r in required if r in completed_keys]) + (1 if content_count >= content_min else 0)) / max(total_criteria, 1) * 100

    # Phase 4 has a graduation score check
    if phase_num == 4:
        grad_min = criteria.get("graduation_score_minimum", 60)
        if score >= grad_min:
            met.append(f"graduation_score>={grad_min}")
        else:
            unmet.append(f"graduation_score>={grad_min} (current: {score:.0f})")

    ready = len(unmet) == 0
    return {"ready": ready, "score": round(score, 1), "met": met, "unmet": unmet}


async def advance_phase(business: Business, session: AsyncSession) -> dict:
    """Advance business to the next phase if graduation criteria are met.

    Returns dict with 'advanced' (bool), 'from_phase', 'to_phase', 'graduation_check'.
    """
    check = await check_phase_graduation(business, session)
    current = business.current_phase

    if not check["ready"]:
        return {"advanced": False, "from_phase": current, "to_phase": current, "graduation_check": check}

    if current >= 4:
        # Graduate from the program
        business.graduation_date = datetime.now(timezone.utc)
        business.current_phase = 5  # Post-graduation

        # Complete the phase record
        result = await session.execute(
            select(RoadmapPhase).where(
                RoadmapPhase.business_id == business.id,
                RoadmapPhase.phase_number == 4,
            )
        )
        phase_record = result.scalar_one_or_none()
        if phase_record:
            phase_record.status = "completed"
            phase_record.graduation_score = check["score"]
            phase_record.completed_at = datetime.now(timezone.utc)

        await session.flush()
        return {"advanced": True, "from_phase": 4, "to_phase": 5, "graduation_check": check, "graduated": True}

    next_phase = current + 1

    # Complete current phase
    result = await session.execute(
        select(RoadmapPhase).where(
            RoadmapPhase.business_id == business.id,
            RoadmapPhase.phase_number == current,
        )
    )
    current_record = result.scalar_one_or_none()
    if current_record:
        current_record.status = "completed"
        current_record.graduation_score = check["score"]
        current_record.completed_at = datetime.now(timezone.utc)

    # Activate next phase
    result = await session.execute(
        select(RoadmapPhase).where(
            RoadmapPhase.business_id == business.id,
            RoadmapPhase.phase_number == next_phase,
        )
    )
    next_record = result.scalar_one_or_none()
    if next_record:
        next_record.status = "active"
        next_record.started_at = datetime.now(timezone.utc)

    business.current_phase = next_phase
    await session.flush()

    log.info("Business %s advanced from phase %d to %d", business.slug, current, next_phase)
    return {"advanced": True, "from_phase": current, "to_phase": next_phase, "graduation_check": check}


async def generate_phase_tasks(business: Business, session: AsyncSession) -> list[dict]:
    """Generate the next set of tasks based on the current roadmap day and phase.

    This is called by the orchestrator to determine what work to do tonight.
    Returns a list of task dicts compatible with the operating plan format.
    """
    day = business.roadmap_day or 0
    phase = business.current_phase or 0

    if phase < 1:
        return []

    if phase >= 5:
        # Post-graduation — delegate to ongoing_optimizer
        return []

    # Determine which week group we're in
    week_group = _week_group_for_day(phase, day)
    if not week_group:
        return []

    # Get tasks for this week group
    tasks = get_phase_tasks(phase, week_group)
    if not tasks:
        return []

    # Filter out already-completed milestones
    result = await session.execute(
        select(Milestone.key).where(
            Milestone.business_id == business.id,
            Milestone.status.in_(["completed", "in_progress"]),
        )
    )
    done_or_active = {row[0] for row in result.all()}

    eligible = [t for t in tasks if t["key"] not in done_or_active]

    if not eligible:
        return []

    # Prioritize tasks whose milestones are overdue
    overdue_keys = set()
    if day > 0:
        overdue_result = await session.execute(
            select(Milestone.key).where(
                Milestone.business_id == business.id,
                Milestone.status != "completed",
                Milestone.due_day != None,  # noqa: E711
                Milestone.due_day < day,
            )
        )
        overdue_keys = {row[0] for row in overdue_result.all()}

    # Sort: overdue first, then by original order
    eligible.sort(key=lambda t: (0 if t["key"] in overdue_keys else 1))

    # Pick one task per cycle (consistent with working day model)
    task = eligible[0]
    context_suffix = ""
    if business.website_summary:
        context_suffix = f" Existing site context: {business.website_summary}"
    elif business.website_url:
        context_suffix = f" Existing site URL: {business.website_url}"

    return [{
        "key": task["key"],
        "output_key": task["key"],
        "kind": "roadmap",
        "area": task["area"],
        "action": task["action"],
        "title": task["title"],
        "status_label": f"Phase {phase}: {task['title']}",
        "brief": task["description"],
        "description": f"{task['description']} Business context: {business.description}.{context_suffix}",
        "expected_output": task["title"],
        "depends_on": [],
        "queue_status": "pending",
        "duration_days": task.get("duration_days", 1),
        "days_remaining": task.get("duration_days", 1),
        "working_days_required": task.get("duration_days", 1),
        "queue_task_key": task["key"],
        "phase": phase,
        "milestone_key": task["key"],
    }]


def _week_group_for_day(phase: int, day: int) -> str | None:
    """Map a roadmap day to the daily task group within a phase.

    PHASE_TASKS now uses ``day_N`` keys for daily granularity.
    Day 1 is handled by operating_plan.py (initial cycle), so Phase 1
    tasks start at day_2.
    """
    key = f"day_{day}"
    phase_tasks = PHASE_TASKS.get(phase, {})
    if key in phase_tasks:
        return key
    return None


def _find_milestone_definition(key: str) -> dict | None:
    """Look up a milestone definition from PHASE_MILESTONES by key."""
    for phase_num, ms_list in PHASE_MILESTONES.items():
        for m in ms_list:
            if m["key"] == key:
                return {"phase_number": phase_num, **m}
    return None


async def complete_milestone(business: Business, milestone_key: str, session: AsyncSession, evidence: dict | None = None) -> bool:
    """Mark a milestone as completed, creating the record if it doesn't exist yet."""
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.key == milestone_key,
        )
    )
    milestone = result.scalar_one_or_none()
    if not milestone:
        # Milestone record missing (e.g. created before this milestone was added).
        # Look up definition and create it on the fly so completion isn't silently dropped.
        defn = _find_milestone_definition(milestone_key)
        if not defn:
            log.warning("Milestone definition not found for key %s — skipping", milestone_key)
            return False
        milestone = Milestone(
            business_id=business.id,
            key=milestone_key,
            title=defn.get("title", milestone_key),
            category=defn.get("category", "deliverable"),
            phase_number=defn["phase_number"],
            due_day=defn.get("due_day"),
            status="pending",
        )
        session.add(milestone)
        await session.flush()
        log.info("Created missing milestone record %s for %s", milestone_key, business.slug)

    milestone.status = "completed"
    milestone.completed_at = datetime.now(timezone.utc)
    if evidence:
        milestone.evidence_json = evidence

    await session.flush()
    log.info("Milestone %s completed for %s", milestone_key, business.slug)
    return True


async def get_roadmap_summary(business: Business, session: AsyncSession) -> dict:
    """Return the full roadmap summary for a business."""
    phases_result = await session.execute(
        select(RoadmapPhase).where(RoadmapPhase.business_id == business.id).order_by(RoadmapPhase.phase_number)
    )
    phases = phases_result.scalars().all()

    milestones_result = await session.execute(
        select(Milestone).where(Milestone.business_id == business.id).order_by(Milestone.phase_number, Milestone.due_day)
    )
    milestones = milestones_result.scalars().all()

    phase_data = []
    for p in phases:
        phase_milestones = [m for m in milestones if m.phase_number == p.phase_number]
        completed_count = sum(1 for m in phase_milestones if m.status == "completed")
        phase_data.append({
            "phase_number": p.phase_number,
            "phase_name": p.phase_name,
            "status": p.status,
            "graduation_score": p.graduation_score,
            "started_at": p.started_at.isoformat() if p.started_at else None,
            "completed_at": p.completed_at.isoformat() if p.completed_at else None,
            "milestones_total": len(phase_milestones),
            "milestones_completed": completed_count,
            "milestones": [
                {
                    "key": m.key,
                    "title": m.title,
                    "category": m.category,
                    "status": m.status,
                    "target_value": m.target_value,
                    "current_value": m.current_value,
                    "due_day": m.due_day,
                    "completed_at": m.completed_at.isoformat() if m.completed_at else None,
                }
                for m in phase_milestones
            ],
        })

    return {
        "roadmap_day": business.roadmap_day,
        "current_phase": business.current_phase,
        "graduation_date": business.graduation_date.isoformat() if business.graduation_date else None,
        "total_days": 60,
        "progress_pct": min(100.0, round((business.roadmap_day or 0) / 60 * 100, 1)),
        "phases": phase_data,
    }


async def get_next_actions(business: Business, session: AsyncSession) -> list[dict]:
    """Return the next recommended actions for this business based on roadmap state."""
    phase = business.current_phase or 0
    day = business.roadmap_day or 0

    if phase >= 5:
        return [{"action": "Post-graduation mode active", "detail": "Adaptive optimization is selecting tasks automatically."}]

    if phase < 1:
        return [{"action": "Start your program", "detail": "Create your first business to begin the 90-day incubator."}]

    actions = []

    # Check incomplete milestones for current phase
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.phase_number == phase,
            Milestone.status != "completed",
        ).order_by(Milestone.due_day)
    )
    pending = result.scalars().all()

    for m in pending[:3]:  # Top 3 upcoming milestones
        overdue = m.due_day and day > m.due_day
        actions.append({
            "action": m.title,
            "detail": f"{'OVERDUE: ' if overdue else ''}Due by day {m.due_day}" if m.due_day else "In progress",
            "milestone_key": m.key,
            "overdue": overdue,
        })

    # Check graduation readiness
    check = await check_phase_graduation(business, session)
    if check["ready"]:
        actions.insert(0, {
            "action": f"Phase {phase} graduation ready!",
            "detail": f"Score: {check['score']}%. You can advance to Phase {phase + 1 if phase < 4 else 'graduation'}.",
            "graduation_ready": True,
        })

    return actions
