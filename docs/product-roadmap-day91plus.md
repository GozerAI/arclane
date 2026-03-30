# Arclane Product Roadmap: Day 91+ Forever Partner & Beyond

> **Document scope:** Strategic roadmap for the Forever Partner mode (Day 91+), expansion revenue, retention mechanics, and the 24-month product vision. Audience: Arclane product team and internal stakeholders.

---

## 1. Executive Summary

At Day 90, something fundamental changes. The 90-day program is a fixed-duration, deliverable-driven experience. It has a clear start, a clear end, and a graduation moment. That structure is what makes it legible and sellable to early-stage founders — they can see exactly what they're buying.

Forever Partner mode is a different product. It has no fixed end. It has no deliverable schedule. It operates on a single governing principle: **the AI picks the highest-impact task for this business, tonight.**

The shift from program to partner is the central retention mechanic in Arclane. Founders don't cancel because they have graduating — they continue because canceling means giving up an AI that has accumulated 90 days of context about their business, their market, their customers, and their competitive position. That context compounds. The longer a founder stays, the harder it becomes to replicate what the system knows elsewhere.

The Forever Partner value proposition is not features. It is **accumulated intelligence applied nightly.**

---

## 2. Forever Partner Feature Set

### 2.1 Adaptive Task Selection

The AI scores business health across 8 dimensions each night (market position, content velocity, revenue health, competitive standing, operational stability, growth trajectory, team readiness, investor readiness) and selects the single highest-impact task from a prioritized queue.

The founder does not choose the task. The system does. This is intentional — decision fatigue about "what should the AI do tonight" is eliminated entirely. The founder's only job is to review what arrived in the morning.

Task selection is transparent: the dashboard shows the health scores, the chosen task, and the reasoning. Founders can override the selection if they want something specific — and that override is logged and learned from.

**Implementation status:** Health scoring architecture in place via C-Suite integration. Adaptive selection logic is the primary Phase 1 Forever Partner build.

---

### 2.2 Revenue Attribution

A lightweight UTM + webhook integration layer that connects Arclane's outreach assets and landing pages to downstream revenue signals.

- **UTM auto-tagging:** All outreach templates, ad briefs, and landing page CTAs generated during the program are tagged with structured UTM parameters
- **Stripe webhook integration:** Founders connect their Stripe account; conversion events are mapped back to Arclane-generated assets
- **Shopify integration:** Same attribution model for e-commerce founders
- **Multi-touch attribution:** The system tracks first-touch, last-touch, and linear attribution across the assets it built

**Why this matters for retention:** When Arclane can show "this email sequence generated $4,200 last month," cancellation becomes irrational. Revenue attribution is the single strongest retention mechanism in the product.

---

### 2.3 Competitive Monitoring

Weekly automated checks across the founder's top 5 competitors (established during Phase 1). The system monitors:

- Landing page messaging changes (copy diff alerts)
- New content published (blog, social, YouTube)
- Pricing page changes
- App store rating trends (for SaaS competitors)
- Job posting velocity (team growth signals)

Delivered as a weekly competitive intelligence brief in the dashboard and via email digest.

---

### 2.4 Content Calendar (Rolling 30-Day)

The 30-day content calendar established in Phase 2 becomes a permanent rolling system. Each week, the AI refreshes the next 7 days of content recommendations based on:

- Trending topics in the founder's market
- Recent competitor content gaps
- Seasonal relevance signals
- Engagement data from previously published content (if analytics are connected)

Auto-topic generation means founders never face a blank content planning session. The queue is always full.

---

### 2.5 Financial Health Tracking

Monthly automated update to the financial model built in Phase 1. The founder inputs 3 numbers (revenue, new customers, churn count) and the AI recalculates:

- Burn rate vs. projection
- Unit economics trend (CAC, LTV, payback period)
- Runway projection
- Variance analysis against Phase 1 model

Over time, this creates a longitudinal financial record that becomes genuinely useful for investor conversations — 12 months of tracked unit economics is a story no first-meeting pitch deck can replicate.

---

### 2.6 Weekly Digest Email

Every Monday morning, the founder receives a single automated email summarizing:

- What the AI did last week (tasks completed, assets delivered)
- Key signals from the week (competitive changes, content performance if analytics connected, financial health flag if triggered)
- What the AI plans to do this week (top 3 tasks in queue)
- One recommended action for the founder (not AI-executed — something only they can do)

The digest is not a marketing email. It is a business operating report. It is the artifact that keeps Arclane top of mind even in weeks when a founder is heads-down and not checking the dashboard.

---

### 2.7 Cohort Benchmarking

Anonymous peer comparison across Arclane founders at the same stage, in the same industry vertical, on similar plans. Benchmarking surfaces:

- Content velocity (how many assets does the average peer publish per month?)
- Landing page conversion rate range (anonymized percentile buckets)
- Growth experiment adoption rate
- Credit utilization patterns

**Why this matters:** Benchmarking creates FOMO-driven upgrade pressure organically. A founder who sees that Growth-plan peers are running 3x more experiments is more likely to upgrade than one who only sees their own usage.

---

## 3. Six-Month Product Roadmap (Q2–Q3 2026)

### Q2 2026 (April – June): Foundation of Forever Partner

| Milestone | Target Month | Description |
|---|---|---|
| Forever Partner mode launch | April 2026 | Adaptive task selection live for all graduating founders |
| Revenue attribution v1 | April 2026 | Stripe webhook + UTM auto-tagging on all generated assets |
| Weekly digest email | May 2026 | Automated Monday digest live for all active subscribers |
| Competitive monitoring v1 | May 2026 | Weekly competitor brief for top 5 competitors |
| Rolling content calendar | June 2026 | Auto-refreshing 30-day content queue post-graduation |
| Financial health tracking | June 2026 | Monthly financial model update flow |

**Q2 success criteria:** ≥70% of graduating founders remain active 30 days post-graduation. Weekly digest open rate ≥45%.

---

### Q3 2026 (July – September): Depth and Expansion

| Milestone | Target Month | Description |
|---|---|---|
| Cohort benchmarking v1 | July 2026 | Anonymous peer comparison across 5 key metrics |
| Shopify revenue attribution | July 2026 | E-commerce founder integration for attribution tracking |
| Team seats | August 2026 | Up to 3 team members can view dashboard and receive digest |
| API access (beta) | August 2026 | Webhook out: Arclane delivers completed assets to founder's tools |
| White-label foundations | September 2026 | Infrastructure for agency/accelerator partner licensing |
| Alumni network v1 | September 2026 | Opt-in peer network for graduated founders (async, curated) |

**Q3 success criteria:** ≥50% of active Forever Partner subscribers retain through Day 180. First white-label partner signed.

---

## 4. Feature Prioritization Matrix

Impact (business value to founder) vs. Effort (engineering + AI complexity). Quadrant: **Top-right = build now; Bottom-left = deprioritize.**

| Feature | Impact | Effort | Quadrant | Priority |
|---|---|---|---|---|
| Adaptive task selection | Very High | Medium | Build Now | P0 |
| Weekly digest email | High | Low | Build Now | P0 |
| Revenue attribution (Stripe) | Very High | Medium | Build Now | P0 |
| Competitive monitoring | High | Medium | Build Now | P1 |
| Rolling content calendar | High | Low | Build Now | P1 |
| Financial health tracking | High | Medium | Build Now | P1 |
| Cohort benchmarking | High | High | Plan for Q3 | P2 |
| Team seats | Medium | Low | Build Now | P1 |
| Shopify attribution | Medium | Medium | Q3 | P2 |
| API access (webhook out) | Medium | Medium | Q3 | P2 |
| White-label licensing | Very High | Very High | Future | P3 |
| Alumni network | Medium | High | Q3 | P3 |
| Mobile app | Low | Very High | Deprioritize | P4 |
| Custom AI persona | Low | Medium | Future | P4 |
| Native CRM integration | Medium | High | Q4 | P3 |
| Investor matching | High | Very High | 2027 | Long-term |

> **Principle:** Features that make the AI's output verifiably valuable (attribution, benchmarking, financial tracking) take priority over features that are visible but not measurably impactful (mobile app, personas).

---

## 5. Retention Mechanisms

Forever Partner retention is built on four compounding forces:

### 5.1 Accumulated Context
The system knows more about this business after 180 days than after 90. Every task completed, every competitor profile updated, every financial model iteration contributes to a richer context layer. A founder who cancels and returns 6 months later starts over. A founder who stays benefits from continuous context compounding. **Switching cost grows over time without any artificial lock-in.**

### 5.2 Asset Dependency
Arclane generates assets that become operationally embedded — email sequences in their ESP, landing pages they're sending traffic to, content calendars their team depends on. These aren't PDFs in a folder. They're live infrastructure. The more assets that are deployed and active, the more disruptive cancellation becomes.

### 5.3 Revenue Attribution Visibility
Once a founder can see that Arclane-generated assets produced measurable revenue, the value question becomes concrete. "Is $99/mo worth it?" becomes "Do these assets generate more than $99/mo?" For the majority of founders who are executing the program seriously, the answer becomes yes. Attribution turns a judgment call into a math problem.

### 5.4 Weekly Digest Habit Loop
The Monday digest creates a habitual engagement touchpoint. Over time, it becomes part of the founder's operating rhythm — not a product notification but a weekly business review. Founders who miss a week of the digest notice it. The digest's presence in the inbox is a weekly reminder of what they'd lose by canceling.

### 5.5 Cohort Social Pressure
Benchmarking data showing that peers are running more experiments, publishing more content, or generating higher conversion rates creates ongoing aspiration pressure. Founders who are below median have a concrete goal to work toward. Founders who are above median want to stay there. Both dynamics increase engagement.

---

## 6. Expansion Revenue Paths

| Revenue Stream | Model | Target Segment | Timeline |
|---|---|---|---|
| **Credit top-ups** | $X per additional credit bundle (e.g., 5 credits for $29) | Any subscriber who hits ceiling mid-cycle | Available at launch |
| **Team seats** | $19/mo per additional seat (viewer + digest access) | Founders with co-founders or employees | Q3 2026 |
| **White-label licensing** | $499–$1,999/mo per partner | Startup accelerators, VC scout programs, business schools | Q3–Q4 2026 |
| **API access** | $99/mo add-on | Technical founders, agencies building on top of Arclane | Q3 2026 |
| **Alumni network** | Included in Growth/Scale, $19/mo for Starter/Pro | All graduated founders | Q3 2026 |
| **Concierge tier** | Custom pricing, human-in-loop review | High-stakes founders (pre-seed closing, Series A prep) | Q4 2026 |
| **Cohort programs** | $1,999 flat fee per cohort of 10 | Accelerators running structured cohort programs | 2027 |

> **White-label is the highest-ceiling expansion path.** A single accelerator program running 2–3 cohorts per year at 20–50 companies per cohort represents $240K–$1.2M ARR from a single partner. The infrastructure investment is high but the unit economics are exceptional.

---

## 7. Success Metrics: Post-Graduation

### 30 Days Post-Graduation (Day 91–120)
- **Target:** ≥75% of graduated founders remain active (at least 1 credit used)
- **Key signal:** Weekly digest open rate ≥50%
- **Leading indicator:** Founders who complete the Graduation Assessment and accept the Q2 Plan retain at 3x the rate of those who skip it

### 60 Days Post-Graduation (Day 121–150)
- **Target:** ≥55% still active
- **Key signal:** At least 1 revenue attribution event tracked (Stripe or Shopify connected)
- **Upgrade pressure:** Growth experiment results start compounding; founders on Starter/Pro see credit ceiling more frequently

### 90 Days Post-Graduation (Day 151–180)
- **Target:** ≥45% still active (this is the "true believer" cohort)
- **Key signal:** Cohort benchmarking engaged (viewed peer comparison report)
- **Net revenue retention target:** ≥110% (expansion via top-ups and upgrades outpaces churn)

### What "Working" Looks Like
A Forever Partner founder at Day 180 should be able to point to:
1. At least 12 AI-generated assets that are deployed and active
2. At least 1 revenue attribution event connected to an Arclane asset
3. A financial model that has been updated at least twice with real data
4. A competitive brief that has flagged at least 1 meaningful competitor change
5. A content calendar that has been used as the source of truth for ≥2 months

If those 5 conditions are true, churn risk drops to near zero. The AI is embedded in operations.

---

## 8. Long-Term Vision: Arclane at 24 Months (March 2028)

### The Business at Scale
By March 2028, Arclane is not primarily a 90-day program. The program is the acquisition vehicle — the structured, legible entry point that converts skeptical founders into believers. The real business is Forever Partner subscriptions — a large base of post-graduation founders who pay monthly for an AI partner that is deeply embedded in their operations.

### What the Product Looks Like
- **The 90-day program** has been refined through 10+ cohort iterations. Success rates are documented. Alumni testimonials are specific and verifiable. The program is a credentialed thing — founders can say "I went through Arclane" the same way they'd say "I went through YC" (at our scale, for our market).
- **Forever Partner** is the primary MRR driver. Median subscriber lifetime is 18+ months. Expansion revenue from team seats, top-ups, and API access represents 25–30% of total ARR.
- **White-label partnerships** with 5–10 accelerators and VC scout programs run co-branded versions of the program, each paying a licensing fee plus per-seat variable.
- **The alumni network** is a genuine peer community of 1,000+ founders who have completed the program. It has its own value independent of the AI — introductions, deal flow, hiring, shared vendor recommendations.
- **The AI itself** has been trained on thousands of real startup trajectories through the program. Its task selection, market research quality, and financial modeling are meaningfully better than they were at launch — not because of model upgrades, but because of accumulated program intelligence.

### The Positioning
Arclane occupies a unique position: **not a tool, not a consultant, not an accelerator — a permanent AI co-founder available to any founder who wants one.** The 90-day program is the proof of concept. Forever Partner is the product. The 24-month vision is the business where those two things are inseparable.

The question Arclane answers for every founder considering cancellation is simple: **"Why would you fire your co-founder?"**

---

## Appendix: Phase Gating and Plan Upgrade Recommendations

| Founder Stage | Recommended Plan | Monthly Investment | Full-Program Cost |
|---|---|---|---|
| Pre-idea / exploring | Preview (Free) | $0 | $0 (3 credits only) |
| Idea validated, first product | Starter → Pro (upgrade at Day 22) | $49 → $99 | ~$247 over 90 days |
| Product live, seeking growth | Pro → Growth (upgrade at Day 45) | $99 → $249 | ~$447 over 90 days |
| Revenue generating, scaling | Growth | $249 | $249/mo, complete in 1 cycle |
| Well-funded, time-sensitive | Scale | $499 | $499/mo, 110 credits spare |

---

*Document version: March 2026 | Arclane Product Team*
