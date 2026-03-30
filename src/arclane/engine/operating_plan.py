"""Post-intake operating plan for launch, provisioning, and code storage."""

from copy import deepcopy
from pathlib import Path

from arclane.core.config import settings
from arclane.engine.intake import build_intake_brief, build_task_plan

_AREA_STATUS_LABELS = {
    "strategy": "Structuring strategy brief",
    "market_research": "Mapping market landscape",
    "content": "Drafting launch asset",
    "operations": "Coordinating launch workflow",
}

_AREA_OUTPUTS = {
    "strategy": "mission and positioning brief",
    "market_research": "market research report",
    "content": "publishable growth asset",
    "operations": "provisioning and launch workflow",
}


def _queue_task(
    *,
    key: str,
    output_key: str,
    kind: str,
    area: str,
    action: str,
    title: str,
    description: str,
    brief: str,
    duration_days: int,
    depends_on: list[str] | None = None,
    supersedes_queue: bool = False,
) -> dict:
    return {
        "key": key,
        "output_key": output_key,
        "kind": kind,
        "area": area,
        "action": action,
        "title": title,
        "status_label": _AREA_STATUS_LABELS.get(area, "Advancing launch plan"),
        "brief": brief,
        "description": description,
        "expected_output": _AREA_OUTPUTS.get(area, "working deliverable"),
        "depends_on": depends_on or [],
        "queue_status": "pending" if kind == "core" else "queued",
        "duration_days": duration_days,
        "days_remaining": duration_days,
        "working_days_required": duration_days,
        "supersedes_queue": supersedes_queue,
        "included_cycles_total": 0,
        "included_cycles_remaining": 0,
    }


def _new_venture_program(description: str, context_suffix: str) -> list[dict]:
    """Day 1 instant-value program: strategy → market research → landing page → tweet.

    All four tasks run sequentially within a single initial cycle so the user
    wakes up to a complete foundation on signup day.  No dependency gating —
    the orchestrator already executes tasks in order, so each output is
    available as context for the next prompt.
    """
    return [
        _queue_task(
            key="core-strategy-01",
            output_key="mission-positioning-brief",
            kind="core",
            area="strategy",
            action="analyze_business_model",
            title="Mission and positioning brief",
            description=(
                "Create the strategic operating brief for this business. "
                f"Define the mission, offer, target customer, wedge, and launch priorities. "
                f"Business context: {description}.{context_suffix}"
            ),
            brief="Produce the core business thesis and mission.",
            duration_days=1,
        ),
        _queue_task(
            key="core-market-01",
            output_key="market-research-report",
            kind="core",
            area="market_research",
            action="competitive_analysis",
            title="Market research report",
            description=(
                "Identify competitors, demand signals, buyer objections, and market gaps worth exploiting. "
                "Produce a complete market report — competitors, positioning gaps, buyer objections, and "
                "opportunities. This runs in the same cycle as the strategy brief, so reference its output. "
                f"Business context: {description}{context_suffix}"
            ),
            brief="Research the market, competitors, and gaps Arclane can exploit quickly.",
            duration_days=1,
        ),
        _queue_task(
            key="core-content-01",
            output_key="starter-landing-page",
            kind="core",
            area="content",
            action="create_initial_content",
            title="Starter landing page draft",
            description=(
                "Create a complete landing page for this business. "
                "Use the strategy brief and market research that were produced earlier in this cycle — "
                "reference the positioning, competitor gaps, and buyer objections to write conversion-ready copy.\n\n"
                "IMPORTANT: Return your response as valid JSON with this exact structure:\n"
                "{\n"
                '  "design": {\n'
                '    "palette": {"primary": "#hex", "secondary": "#hex", "accent": "#hex", "bg": "#hex", "text": "#hex"},\n'
                '    "font": "modern|classic|playful|technical|elegant",\n'
                '    "hero_style": "bold|minimal|stats|lifestyle",\n'
                '    "vibe": "one sentence describing the visual mood"\n'
                "  },\n"
                '  "sections": [\n'
                '    {"type": "hero", "headline": "...", "subheadline": "...", "cta_text": "...", "cta_url": "#signup"},\n'
                '    {"type": "problem", "headline": "...", "points": [{"icon": "emoji", "title": "...", "description": "..."}]},\n'
                '    {"type": "solution", "headline": "...", "features": [{"icon": "emoji", "title": "...", "description": "..."}]},\n'
                '    {"type": "proof", "headline": "...", "items": [{"quote": "...", "author": "...", "role": "..."}]},\n'
                '    {"type": "pricing", "headline": "...", "plans": [{"name": "...", "price": "...", "features": ["..."], "cta": "...", "highlighted": false}]},\n'
                '    {"type": "faq", "headline": "...", "items": [{"question": "...", "answer": "..."}]},\n'
                '    {"type": "cta", "headline": "...", "subheadline": "...", "cta_text": "...", "cta_url": "#signup"}\n'
                "  ]\n"
                "}\n\n"
                "Design rules:\n"
                "- Choose colors that match the product's industry and personality. A coffee brand should feel warm and earthy. "
                "A fintech app should feel clean and trustworthy. A creative tool should feel bold and energetic.\n"
                "- The font style should match the brand tone. 'technical' for dev tools, 'elegant' for luxury, 'playful' for consumer apps.\n"
                "- Include 3-6 sections. Not every type is needed — pick what makes sense for THIS product.\n"
                "- Write real copy, not placeholders. Use the actual business name and specifics from the research.\n"
                "- Testimonials can be aspirational (what a happy customer would say) if none exist yet.\n\n"
                f"Business context: {description}{context_suffix}"
            ),
            brief="Build the first public surface and keep it conversion-focused.",
            duration_days=1,
        ),
        _queue_task(
            key="core-social-01",
            output_key="launch-tweet",
            kind="core",
            area="content",
            action="create_launch_tweet",
            title="Launch announcement tweet",
            description=(
                "Write a tweet for the @araborcloud Twitter account announcing this new business "
                "has launched on Arclane. The tweet should: (1) name the business, (2) describe what "
                "it does in one punchy line, (3) include a link to the landing page, and (4) feel "
                "authentic — not corporate. Keep it under 280 characters. "
                f"Business context: {description}{context_suffix}"
            ),
            brief="Create a launch tweet for the Arclane account announcing this business.",
            duration_days=1,
        ),
    ]


def _existing_business_program(description: str, context_suffix: str) -> list[dict]:
    """Day 1 instant-value program for existing businesses.

    Same philosophy as _new_venture_program: all tasks execute in a single
    initial cycle so the user sees immediate value.
    """
    return [
        _queue_task(
            key="core-strategy-01",
            output_key="offer-diagnosis",
            kind="core",
            area="strategy",
            action="analyze_business_model",
            title="Offer and positioning diagnosis",
            description=(
                "Assess the current offer, target customer, wedge, and positioning weaknesses on the existing site. "
                f"Business context: {description}.{context_suffix}"
            ),
            brief="Produce the business diagnosis and sharpen the wedge.",
            duration_days=1,
        ),
        _queue_task(
            key="core-market-01",
            output_key="market-research-report",
            kind="core",
            area="market_research",
            action="competitive_analysis",
            title="Market research report",
            description=(
                "Assess the current site, identify competitor gaps, and recommend positioning improvements. "
                "This runs in the same cycle as the diagnosis, so reference its output directly. "
                f"Business context: {description}{context_suffix}"
            ),
            brief="Research the market, competitors, and gaps worth attacking first.",
            duration_days=1,
        ),
        _queue_task(
            key="core-content-01",
            output_key="homepage-rewrite",
            kind="core",
            area="content",
            action="create_initial_content",
            title="Homepage rewrite draft",
            description=(
                "Rewrite the homepage offer, sharpen conversion copy, and restructure the public page around a clearer "
                "promise. Use the diagnosis and market research produced earlier in this cycle to write copy that "
                "addresses real competitive gaps and buyer objections. "
                f"Business context: {description}{context_suffix}"
            ),
            brief="Ship a stronger homepage draft without trying to rewrite the whole business at once.",
            duration_days=1,
        ),
        _queue_task(
            key="core-social-01",
            output_key="launch-tweet",
            kind="core",
            area="content",
            action="create_launch_tweet",
            title="Launch announcement tweet",
            description=(
                "Write a tweet for the @araborcloud Twitter account announcing this business "
                "has joined Arclane for an accelerated growth program. The tweet should: "
                "(1) name the business, (2) describe what it does in one punchy line, "
                "(3) include a link to the site, and (4) feel authentic — not corporate. "
                "Keep it under 280 characters. "
                f"Business context: {description}{context_suffix}"
            ),
            brief="Create a launch tweet for the Arclane account announcing this business.",
            duration_days=1,
        ),
    ]


def _default_add_on_offers(description: str, context_suffix: str) -> list[dict]:
    return [
        {
            "key": "deep-market-dive",
            "title": "Deep market dive",
            "detail": "Expand the competitor map, buyer segments, objections, and whitespace before the next core output.",
            "trigger_output_key": "market-research-report",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": True,
            "queue_template": _queue_task(
                key="addon-market-01",
                output_key="deep-market-dive",
                kind="add_on",
                area="market_research",
                action="deep_competitive_analysis",
                title="Deep market dive",
                description=(
                    "Expand the existing market report into a deeper competitor, buyer, and positioning analysis. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Go deeper on the market before returning to the normal ramp queue.",
                duration_days=3,
                supersedes_queue=True,
            ),
        },
        {
            "key": "expanded-competitor-teardown",
            "title": "Expanded competitor teardown",
            "detail": "Break down competitor messaging, offers, weaknesses, and likely go-to-market blind spots.",
            "trigger_output_key": "market-research-report",
            "status": "locked",
            "working_days_required": 2,
            "supersedes_queue": True,
            "queue_template": _queue_task(
                key="addon-market-02",
                output_key="expanded-competitor-teardown",
                kind="add_on",
                area="market_research",
                action="expanded_competitor_teardown",
                title="Expanded competitor teardown",
                description=(
                    "Create a deeper teardown of the top competitors and identify where the business can beat them with "
                    f"clarity or speed. Business context: {description}{context_suffix}"
                ),
                brief="Tear down the most relevant competitors before resuming the main queue.",
                duration_days=2,
                supersedes_queue=True,
            ),
        },
        {
            "key": "landing-page-sprint",
            "title": "Landing page sprint",
            "detail": "Turn the starter page into a more complete conversion-focused public surface.",
            "trigger_output_key": "starter-landing-page",
            "status": "locked",
            "working_days_required": 2,
            "supersedes_queue": True,
            "queue_template": _queue_task(
                key="addon-content-01",
                output_key="landing-page-sprint",
                kind="add_on",
                area="content",
                action="landing_page_sprint",
                title="Landing page sprint",
                description=(
                    "Expand the starter landing page into a more complete conversion surface with proof, objections, "
                    f"CTA variants, and better flow. Business context: {description}{context_suffix}"
                ),
                brief="Deepen the landing page before the next queued output.",
                duration_days=2,
                supersedes_queue=True,
            ),
        },
        {
            "key": "social-batch-pack",
            "title": "Social batch pack",
            "detail": "Turn the first social asset into a short batch the founder can schedule over several days.",
            "trigger_output_key": "launch-tweet",
            "status": "locked",
            "working_days_required": 2,
            "supersedes_queue": True,
            "queue_template": _queue_task(
                key="addon-content-02",
                output_key="social-batch-pack",
                kind="add_on",
                area="content",
                action="social_batch_pack",
                title="Social batch pack",
                description=(
                    "Expand the first growth asset into a short social batch the founder can publish over several days. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Create a compact social batch before the normal queue resumes.",
                duration_days=2,
                supersedes_queue=True,
            ),
        },
        {
            "key": "icp-deep-dive",
            "title": "ICP deep dive",
            "detail": "Expand the ICP into 3 fully-developed buyer personas with demographics, trigger events, and real-language quotes.",
            "trigger_output_key": "icp-document",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-icp-01",
                output_key="icp-deep-dive",
                kind="add_on",
                area="strategy",
                action="icp_deep_dive",
                title="ICP deep dive — 3 full personas",
                description=(
                    "Expand the ICP document into 3 fully developed buyer personas. For each: "
                    "demographics, company profile, buying process, top 3 trigger events, "
                    "objection stack, decision criteria, 10 real-language quotes from the market. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Go deeper on the buyer before the next cycle.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "founding-story-video",
            "title": "Founding story video script",
            "detail": "Turn the founding story into a 90-second video script with on-screen cues and a voiceover version.",
            "trigger_output_key": "founding-story",
            "status": "locked",
            "working_days_required": 2,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-03",
                output_key="founding-story-video",
                kind="add_on",
                area="content",
                action="founding_story_video",
                title="Founding story video script",
                description=(
                    "Convert the founding story into a 90-second video script. Deliverables: "
                    "(1) Full video script with on-screen text cues, spoken word, and b-roll suggestions. "
                    "(2) 60-second edit. (3) Voiceover-only version for audio contexts. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Produce a video script from the founding story.",
                duration_days=2,
                supersedes_queue=False,
            ),
        },
        {
            "key": "competitor-matrix-expansion",
            "title": "Competitor positioning matrix expansion",
            "detail": "Expand the matrix to 10 competitors with a visual positioning map and a sales-ready battle card.",
            "trigger_output_key": "competitive-positioning-matrix",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-market-03",
                output_key="competitor-matrix-expansion",
                kind="add_on",
                area="market_research",
                action="expanded_positioning_matrix",
                title="Competitive positioning matrix expansion",
                description=(
                    "Expand the competitive matrix to 10 competitors across 10 attributes. "
                    "Add a positioning map (2x2 axes) and a one-page battle card formatted for sales use. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Deeper competitive intelligence for sales and positioning.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "seo-content-plan",
            "title": "Full SEO content plan",
            "detail": "A 6-month SEO roadmap: 24 blog post briefs organized into a publishing calendar with traffic projections.",
            "trigger_output_key": "seo-baseline",
            "status": "locked",
            "working_days_required": 4,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-04",
                output_key="seo-content-plan",
                kind="add_on",
                area="content",
                action="seo_content_plan",
                title="Full 6-month SEO content plan",
                description=(
                    "Build a 6-month SEO content plan: 24 post briefs (title, keyword, intent, outline, "
                    "internal link plan), a monthly publishing calendar, and estimated traffic per post. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Full SEO publishing roadmap for 6 months.",
                duration_days=4,
                supersedes_queue=False,
            ),
        },
        {
            "key": "landing-page-variant-c",
            "title": "Landing page variant C",
            "detail": "A third landing page variant targeting a different segment or angle for multi-variant A/B testing.",
            "trigger_output_key": "landing-page-sprint",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-05",
                output_key="landing-page-variant-c",
                kind="add_on",
                area="content",
                action="landing_page_variant",
                title="Landing page variant C — alternate segment",
                description=(
                    "Produce a third landing page variant targeting a different buyer segment or angle. "
                    "Full copy: headline, proof, pricing block, FAQ, CTA. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Third landing page variant for multi-variant testing.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "financial-model-investor",
            "title": "Full financial model + investor brief",
            "detail": "18-month financial narrative with 5 scenarios, cap table template, burn rate, and investor-formatted assumptions.",
            "trigger_output_key": "financial-model",
            "status": "locked",
            "working_days_required": 4,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-finance-01",
                output_key="financial-model-investor",
                kind="add_on",
                area="finance",
                action="investor_financial_model",
                title="Full investor-grade financial model",
                description=(
                    "Produce an 18-month financial narrative: 5 scenarios, cap table template brief, "
                    "burn rate + runway table, and a narrated walk-through of all assumptions. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Investor-grade financial model and narrative.",
                duration_days=4,
                supersedes_queue=False,
            ),
        },
        {
            "key": "email-sequence-extension",
            "title": "Email sequence extension — emails 6–15",
            "detail": "10 additional lifecycle emails: post-conversion onboarding, upsell nurture, re-engagement, and win-back.",
            "trigger_output_key": "email-nurture-sequence",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-06",
                output_key="email-sequence-extension",
                kind="add_on",
                area="content",
                action="email_sequence_extension",
                title="Email sequence extension — 10 more emails",
                description=(
                    "Extend the 5-email nurture into a 15-email full lifecycle system. "
                    "Emails 6-10: post-conversion onboarding + upsell nurture. "
                    "Emails 11-15: re-engagement, win-back, anniversary. "
                    "Each with subject, preview text, body, CTA. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Complete 15-email lifecycle system.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "outreach-campaign-pack",
            "title": "Outreach campaign pack",
            "detail": "30-day multi-channel outreach campaign: 5 cold email sequences + 3 LinkedIn sequences + DM cadence.",
            "trigger_output_key": "cold-outreach-templates",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-07",
                output_key="outreach-campaign-pack",
                kind="add_on",
                area="content",
                action="outreach_campaign_pack",
                title="30-day outreach campaign pack",
                description=(
                    "Build a full outreach campaign: 5 cold email sequences (4 emails each), "
                    "3 LinkedIn sequences, DM follow-up cadence, and a 30-day outreach calendar. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Full 30-day multi-channel outreach campaign.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "paid-acquisition-creative",
            "title": "Paid acquisition creative pack",
            "detail": "10 ad creative briefs + 3 landing page variants for paid traffic + a testing matrix.",
            "trigger_output_key": "paid-acquisition-brief",
            "status": "locked",
            "working_days_required": 4,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-08",
                output_key="paid-acquisition-creative",
                kind="add_on",
                area="content",
                action="paid_acquisition_creative",
                title="Paid acquisition creative pack",
                description=(
                    "Produce 10 ad creative briefs (headline, hook, body, CTA, targeting note per ad), "
                    "3 paid landing page variants, and a testing matrix for the first 2 weeks. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Ad creatives + landing pages for paid acquisition.",
                duration_days=4,
                supersedes_queue=False,
            ),
        },
        {
            "key": "referral-launch-kit",
            "title": "Referral program launch kit",
            "detail": "Ready-to-send referral launch assets: announcement email, in-app prompt, social post, advocate welcome email.",
            "trigger_output_key": "referral-program",
            "status": "locked",
            "working_days_required": 2,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-09",
                output_key="referral-launch-kit",
                kind="add_on",
                area="content",
                action="referral_launch_kit",
                title="Referral program launch kit",
                description=(
                    "Produce all assets to launch the referral program: announcement email, "
                    "in-app prompt copy, social post, advocate welcome email, and 30-day activation calendar. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Full asset kit to launch the referral program.",
                duration_days=2,
                supersedes_queue=False,
            ),
        },
        {
            "key": "customer-discovery-sprint",
            "title": "Customer discovery sprint",
            "detail": "5 synthesized customer discovery reports plus pattern analysis: top 3 validated pains, 3 surprises, positioning recs.",
            "trigger_output_key": "customer-discovery-interview-guide",
            "status": "locked",
            "working_days_required": 4,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-research-01",
                output_key="customer-discovery-sprint",
                kind="add_on",
                area="market_research",
                action="customer_discovery_sprint",
                title="Customer discovery sprint — 5 interview syntheses",
                description=(
                    "Synthesize 5 customer discovery interview reports. Produce: pattern analysis, "
                    "top 3 validated pain points, top 3 surprises, and a positioning adjustment recommendation. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Deep customer discovery synthesis and positioning update.",
                duration_days=4,
                supersedes_queue=False,
            ),
        },
        {
            "key": "objection-library",
            "title": "Objection library",
            "detail": "15-objection library with 3-tier responses per objection, organized by sales stage, plus a role-play training guide.",
            "trigger_output_key": "objection-handling-playbook",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-strategy-01",
                output_key="objection-library",
                kind="add_on",
                area="strategy",
                action="objection_library",
                title="Full 15-objection sales library",
                description=(
                    "Expand the objection playbook to 15 objections with 3-tier responses each "
                    "(email reply, call script, landing page reframe), organized by sales stage, "
                    "plus a training role-play guide. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Comprehensive objection library for the whole sales team.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "partnership-outreach-campaign",
            "title": "Partnership outreach campaign",
            "detail": "Personalized 3-email outreach sequences for each of the 5 partnership targets + a co-marketing proposal template.",
            "trigger_output_key": "partnership-targets",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-10",
                output_key="partnership-outreach-campaign",
                kind="add_on",
                area="content",
                action="partnership_outreach_campaign",
                title="Partnership outreach campaign",
                description=(
                    "For each of the 5 partnership targets: write a personalized 3-email sequence "
                    "(opener, follow-up, breakup email) plus a co-marketing proposal template. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Full outreach campaign for all 5 partnership targets.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "podcast-guest-campaign",
            "title": "Podcast guest campaign",
            "detail": "25 target shows + personalized pitch per tier + post-appearance content repurpose plan.",
            "trigger_output_key": "podcast-pitch-kit",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-11",
                output_key="podcast-guest-campaign",
                kind="add_on",
                area="content",
                action="podcast_guest_campaign",
                title="Podcast guest campaign — 25 shows",
                description=(
                    "Identify 25 target podcasts with audience estimates. Write personalized pitches "
                    "for 3 tiers (top 5, next 10, remaining 10). Add a post-appearance repurpose plan: "
                    "clips, newsletter recap, social thread. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Full podcast guest campaign for 25 shows.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "case-study-pack",
            "title": "Case study production pack",
            "detail": "3 case study formats from the same data: long-form PDF version, sales email version, social carousel brief.",
            "trigger_output_key": "case-study-template",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-12",
                output_key="case-study-pack",
                kind="add_on",
                area="content",
                action="case_study_pack",
                title="Case study production pack — 3 formats",
                description=(
                    "Produce the first case study in 3 formats: "
                    "long-form (600 words), sales email (200 words), social carousel (6-slide brief). "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Case study in 3 ready-to-deploy formats.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "affiliate-recruit-pack",
            "title": "Affiliate recruit pack",
            "detail": "Outreach email to 20 affiliate prospects + affiliate welcome kit + 90-day activation calendar.",
            "trigger_output_key": "affiliate-program-blueprint",
            "status": "locked",
            "working_days_required": 2,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-13",
                output_key="affiliate-recruit-pack",
                kind="add_on",
                area="content",
                action="affiliate_recruit_pack",
                title="Affiliate recruit pack — 20 prospects",
                description=(
                    "Produce an affiliate recruit pack: outreach email to 20 prospect affiliates, "
                    "affiliate welcome kit (program overview, commissions, creative assets brief, FAQ), "
                    "and a 90-day activation calendar. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Everything needed to recruit and onboard first affiliates.",
                duration_days=2,
                supersedes_queue=False,
            ),
        },
        {
            "key": "growth-experiment-portfolio",
            "title": "Growth experiment portfolio",
            "detail": "5 growth experiments ready to run in parallel, each fully designed with hypothesis, channel, test spec, and success criteria.",
            "trigger_output_key": "growth-experiment-design",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-strategy-02",
                output_key="growth-experiment-portfolio",
                kind="add_on",
                area="strategy",
                action="growth_experiment_portfolio",
                title="Growth experiment portfolio — 5 experiments",
                description=(
                    "Design 5 growth experiments ready to run: each with hypothesis, channel or lever, "
                    "variant design, sample size, measurement plan, and decision criteria. "
                    "Priority ranked with rationale. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Portfolio of 5 growth experiments ready to execute.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "investor-outreach-campaign",
            "title": "Investor outreach campaign",
            "detail": "20 investor archetypes + personalized cold email per archetype + 2-email follow-up sequence + investor objection guide.",
            "trigger_output_key": "investor-brief",
            "status": "locked",
            "working_days_required": 4,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-finance-02",
                output_key="investor-outreach-campaign",
                kind="add_on",
                area="finance",
                action="investor_outreach_campaign",
                title="Investor outreach campaign — 20 targets",
                description=(
                    "Produce a targeted investor outreach campaign: 20 investor archetypes that fit, "
                    "personalized 3-paragraph cold email per archetype, 2-email follow-up sequence, "
                    "and an investor objection response guide. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Full investor outreach campaign with personalized messaging.",
                duration_days=4,
                supersedes_queue=False,
            ),
        },
        {
            "key": "full-brand-identity",
            "title": "Full brand identity system",
            "detail": "Complete brand brief: positioning statement, personality, voice guide with 15 examples, messaging hierarchy, naming conventions.",
            "trigger_output_key": "brand-voice-guide",
            "status": "locked",
            "working_days_required": 5,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-content-14",
                output_key="full-brand-identity",
                kind="add_on",
                area="content",
                action="full_brand_identity",
                title="Full brand identity system",
                description=(
                    "Produce the complete brand identity brief: positioning statement, "
                    "5 personality attributes, voice + tone guidelines with 15 examples, "
                    "messaging hierarchy (primary/secondary/tertiary claims), "
                    "naming conventions, and a do/don't usage guide. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Complete brand identity system for the whole team.",
                duration_days=5,
                supersedes_queue=False,
            ),
        },
        {
            "key": "hiring-campaign-pack",
            "title": "Hiring campaign pack",
            "detail": "Full recruiting campaign for the first hire: 3 sourcing posts, structured interview scorecard, offer letter template, 30-day onboarding plan.",
            "trigger_output_key": "first-hire-profile",
            "status": "locked",
            "working_days_required": 3,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-ops-01",
                output_key="hiring-campaign-pack",
                kind="add_on",
                area="operations",
                action="hiring_campaign_pack",
                title="Hiring campaign pack",
                description=(
                    "Produce a full recruiting campaign for the first hire: "
                    "3 sourcing posts (LinkedIn, job board, community), "
                    "structured interview scorecard (6 competencies, 2 questions each), "
                    "offer letter template, and a 30-day onboarding plan. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Everything needed to recruit, evaluate, and onboard the first hire.",
                duration_days=3,
                supersedes_queue=False,
            ),
        },
        {
            "key": "post-graduation-roadmap",
            "title": "90-day post-graduation roadmap",
            "detail": "Fully detailed 90-day operating plan: week-by-week objectives, KPI targets, risk register, and a board-ready summary slide.",
            "trigger_output_key": "q2-roadmap",
            "status": "locked",
            "working_days_required": 5,
            "supersedes_queue": False,
            "queue_template": _queue_task(
                key="addon-strategy-03",
                output_key="post-graduation-roadmap",
                kind="add_on",
                area="strategy",
                action="post_graduation_roadmap",
                title="90-day post-graduation roadmap",
                description=(
                    "Produce a detailed 90-day operating plan for the post-graduation period: "
                    "week-by-week objectives, working-day-budgeted task plan, KPI targets by month, "
                    "risk register with contingency plans, and a board-ready 1-slide summary. "
                    f"Business context: {description}{context_suffix}"
                ),
                brief="Complete post-graduation operating plan for the next 90 days.",
                duration_days=5,
                supersedes_queue=False,
            ),
        },
    ]


def enqueue_add_on(plan: dict, add_on_key: str, phase: int = 0, day: int = 0) -> dict:
    """Insert a purchased add-on into the queue ahead of pending core work."""
    updated = deepcopy(plan)
    offers = updated.get("add_on_offers") or []
    agent_tasks = updated.get("agent_tasks") or []

    offer = next((item for item in offers if item.get("key") == add_on_key), None)
    if not offer or offer.get("status") not in {"available", "locked"}:
        return updated

    queued_task = deepcopy(offer["queue_template"])
    if any(task.get("key") == queued_task["key"] for task in agent_tasks):
        return updated
    queued_task["included_cycles_total"] = int(offer.get("working_days_required", queued_task.get("working_days_required", 0)) or 0)
    queued_task["included_cycles_remaining"] = queued_task["included_cycles_total"]
    queued_task["added_in_phase"] = phase
    queued_task["added_on_day"] = day

    insert_at = next(
        (
            index
            for index, task in enumerate(agent_tasks)
            if task.get("kind") == "core" and task.get("queue_status") in {"pending", "active"}
        ),
        len(agent_tasks),
    )
    agent_tasks.insert(insert_at, queued_task)
    offer["status"] = "purchased"
    updated["agent_tasks"] = agent_tasks
    updated["add_on_offers"] = offers
    return updated


def reserve_included_cycle(plan: dict, task_key: str) -> tuple[dict, bool]:
    """Consume one included cycle from a purchased add-on task if available."""
    updated = deepcopy(plan)
    agent_tasks = updated.get("agent_tasks") or []
    for task in agent_tasks:
        if task.get("key") != task_key:
            continue
        remaining = int(task.get("included_cycles_remaining", 0) or 0)
        if remaining <= 0:
            return updated, False
        task["included_cycles_remaining"] = remaining - 1
        return updated, True
    return updated, False


def build_operating_plan(
    *,
    name: str,
    slug: str,
    description: str,
    template: str | None = None,
    website_url: str | None = None,
    website_summary: str | None = None,
) -> dict:
    """Build the persisted operating plan Arclane uses after intake."""
    intake_brief = build_intake_brief(
        description,
        website_summary=website_summary,
        website_url=website_url,
    )
    task_plan = build_task_plan(
        description,
        template=template,
        website_summary=website_summary,
        website_url=website_url,
    )
    workspace_path = Path(settings.workspaces_root) / slug
    manifest_path = workspace_path / "arclane-workspace.json"
    subdomain = f"{slug}.{settings.domain}"
    email_address = f"{slug}@{settings.email_from_domain}"

    context_suffix = ""
    if website_summary:
        context_suffix = f" Existing site context: {website_summary}"
    elif website_url:
        context_suffix = f" Existing site URL: {website_url}"

    program_type = "existing_business" if website_url else "new_venture"
    agent_tasks = (
        _existing_business_program(description, context_suffix)
        if website_url
        else _new_venture_program(description, context_suffix)
    )

    if website_url:
        user_recommendations = [
            {
                "title": "Approve the homepage rewrite",
                "detail": "Let Arclane sharpen the existing offer before sending more traffic.",
                "task": "Rewrite the homepage with a tighter offer, proof, and call to action.",
            },
            {
                "title": "Launch one acquisition channel",
                "detail": "Pick social or paid acquisition so the system can create work against a real channel.",
                "task": "Create the first acquisition plan and draft channel-ready assets.",
            },
            {
                "title": "Review the mission brief",
                "detail": "Confirm the wedge and target customer so follow-up work stays focused.",
                "task": "Summarize the mission, wedge, and priority customer in one operator brief.",
            },
        ]
    else:
        user_recommendations = [
            {
                "title": "Confirm the core offer",
                "detail": "A sharper promise makes every downstream artifact stronger.",
                "task": "Turn this business idea into one clear offer with a target customer and concrete outcome.",
            },
            {
                "title": "Choose the first public surface",
                "detail": "Arclane can move faster once the initial site or landing page is locked in.",
                "task": "Draft the first landing page and recommend the highest-conversion call to action.",
            },
            {
                "title": "Pick the first growth loop",
                "detail": "Start with one channel and one follow-up path instead of spreading across everything.",
                "task": "Recommend the first acquisition loop with follow-up automation and launch steps.",
            },
        ]

    return {
        "program_type": program_type,
        "working_day_model": {
            "definition": "One working day equals one nightly execution cycle for one business.",
            "cadence": "Arclane advances one queued work unit per night by default.",
            "acceleration_model": "Additional working days can be used to buy more nights of queue work.",
        },
        "intake_brief": intake_brief,
        "agent_tasks": agent_tasks,
        "add_on_offers": _default_add_on_offers(description, context_suffix),
        "user_recommendations": user_recommendations,
        "provisioning": {
            "subdomain": subdomain,
            "mailbox": email_address,
            "public_url": website_url or f"https://{subdomain}",
            "workspace_path": str(workspace_path),
            "steps": [
                {
                    "key": "subdomain",
                    "label": "Route public subdomain",
                    "status": "pending",
                    "detail": f"Create {subdomain} in Caddy and attach it to the tenant container.",
                },
                {
                    "key": "mailbox",
                    "label": "Configure business address",
                    "status": "pending",
                    "detail": f"Register {email_address} as the business contact address and sender identity.",
                },
                {
                    "key": "workspace",
                    "label": "Stage business workspace",
                    "status": "pending",
                    "detail": f"Copy the {task_plan['template']} template into the tenant workspace and track it with a manifest.",
                },
                {
                    "key": "deploy",
                    "label": "Deploy tenant surface",
                    "status": "pending",
                    "detail": "Build and run the tenant container, then switch the public route to the live upstream.",
                },
            ],
        },
        "code_storage": {
            "mode": "workspace_copy",
            "workspace_path": str(workspace_path),
            "manifest_path": str(manifest_path),
            "template": task_plan["template"],
            "strategy": "Store generated business code in an Arclane-owned tenant workspace first, then attach external git later if needed.",
        },
    }
