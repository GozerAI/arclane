"""Run all 18 test prompts through the CLI test harness."""

import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = str(Path(__file__).parent / "cli_test_cycle.py")
RESULTS_DIR = Path(__file__).parent / "prompt_results"
RESULTS_DIR.mkdir(exist_ok=True)

PROMPTS = [
    # --- Existing businesses (content-site) ---
    {"id": 1, "type": "existing", "template": "content-site",
     "url": "https://www.nerdfitness.com",
     "desc": "I run Nerd Fitness, a fitness blog and coaching business for self-described nerds. We have great content but our conversion from reader to paying coaching client is weak. I want to sharpen our offer, get more email signups, and launch a content strategy that drives coaching revenue."},
    {"id": 2, "type": "existing", "template": "content-site",
     "url": "https://copyblogger.com",
     "desc": "We run Copyblogger, a content marketing education platform. Growth has stalled. Need fresh positioning, a content calendar that drives email list growth, and a strategy to sell our courses and community memberships."},
    {"id": 3, "type": "existing", "template": "content-site",
     "url": "https://zenhabits.net",
     "desc": "I run Zen Habits, a minimalist living and productivity blog. We have steady traffic but almost no monetization. I want to explore coaching, a small course, and a tighter email funnel without losing the minimalist brand."},

    # --- Existing businesses (saas-app) ---
    {"id": 4, "type": "existing", "template": "saas-app",
     "url": "https://www.toggl.com/track",
     "desc": "We compete in the time tracking space against Toggl. Our product is built but we cannot get traction. Need help with positioning against incumbents, a freemium-to-paid conversion strategy, and content that ranks for time tracking keywords."},
    {"id": 5, "type": "existing", "template": "saas-app",
     "url": "https://www.lemlist.com",
     "desc": "We are building a cold outreach platform similar to Lemlist. We have the product but zero distribution. Need a go-to-market plan, email templates we can use for our own outreach, and a content strategy targeting sales teams."},
    {"id": 6, "type": "existing", "template": "saas-app",
     "url": "https://www.cal.com",
     "desc": "We are building an open-source scheduling tool competing with Cal.com. Need help positioning as the developer-friendly alternative, building a community strategy, and a pricing model that converts open-source users to paid."},

    # --- Existing businesses (landing-page) ---
    {"id": 7, "type": "existing", "template": "landing-page",
     "url": "https://www.mckinsey.com",
     "desc": "I am launching a boutique strategy consultancy targeting mid-market SaaS companies. Think McKinsey but for Series A-C startups. Need positioning, a landing page that books discovery calls, and thought leadership content."},
    {"id": 8, "type": "existing", "template": "landing-page",
     "url": "https://dribbble.com",
     "desc": "I am a freelance product designer launching a design studio. I have been doing work through Dribbble and referrals but need my own brand. Need a portfolio site, positioning against agencies, and an outreach strategy."},
    {"id": 9, "type": "existing", "template": "landing-page",
     "url": "https://www.eventbrite.com",
     "desc": "I am starting a boutique corporate event planning service focused on tech company offsites. Need a landing page, competitor analysis vs Eventbrite and platforms, and a content strategy targeting VP-level decision makers."},

    # --- New businesses (content-site) ---
    {"id": 10, "type": "new", "template": "content-site",
     "desc": "AI-powered meal planning for people with dietary restrictions. Generate weekly meal plans, grocery lists, and recipes customized for allergies, keto, vegan, etc. Subscription model with a free tier."},
    {"id": 11, "type": "new", "template": "content-site",
     "desc": "A content platform teaching non-technical founders how to evaluate and buy AI tools for their business. Reviews, comparison guides, and a paid newsletter with vendor assessments."},
    {"id": 12, "type": "new", "template": "content-site",
     "desc": "A parenting blog focused on screen time management for kids aged 5-12. Evidence-based advice, app recommendations, and a community membership with weekly challenges."},

    # --- New businesses (saas-app) ---
    {"id": 13, "type": "new", "template": "saas-app",
     "desc": "A lightweight CRM for solo consultants and freelancers. No bloat, just contacts, deals, follow-up reminders, and a simple pipeline view. 19 dollars per month, no free tier, 14-day trial."},
    {"id": 14, "type": "new", "template": "saas-app",
     "desc": "An AI-powered code review tool that explains PRs in plain English for non-technical stakeholders. Integrates with GitHub, generates executive summaries of what changed and why. Team pricing."},
    {"id": 15, "type": "new", "template": "saas-app",
     "desc": "A bookkeeping automation tool for Etsy and Shopify sellers. Auto-imports transactions, categorizes expenses, generates profit and loss reports, and preps quarterly tax estimates."},

    # --- New businesses (landing-page) ---
    {"id": 16, "type": "new", "template": "landing-page",
     "desc": "A fractional CTO service for non-technical founders raising their seed round. I do technical due diligence, help hire the first engineer, and build the MVP architecture. 5000 dollars per month retainer."},
    {"id": 17, "type": "new", "template": "landing-page",
     "desc": "An AI training workshop business targeting marketing teams at mid-size companies. Half-day workshops teaching teams to use AI for copywriting, SEO, and social media. 3500 dollars per workshop."},
    {"id": 18, "type": "new", "template": "landing-page",
     "desc": "A subscription snack box curated for remote workers. Brain food, energy snacks, and focus drinks delivered monthly. 39 dollars per month with a quarterly option."},
]

SKIP = {7, 13}  # Already tested


def run_prompt(prompt):
    """Run a single prompt through the CLI test."""
    pid = prompt["id"]
    args = [sys.executable, SCRIPT]

    if prompt["type"] == "existing":
        args += ["--existing", "--url", prompt["url"]]
    else:
        args += ["--new"]

    args += ["-d", prompt["desc"], "-t", prompt["template"]]

    output_file = RESULTS_DIR / f"prompt_{pid:02d}.txt"

    print(f"\n{'='*60}")
    print(f"  PROMPT {pid}/18 — {prompt['type']} / {prompt['template']}")
    print(f"  {prompt['desc'][:70]}...")
    print(f"{'='*60}")

    start = time.time()
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            cwd=str(Path(__file__).parent.parent),
        )
        elapsed = time.time() - start

        output = result.stdout + ("\n\nSTDERR:\n" + result.stderr if result.stderr else "")
        output_file.write_text(output, encoding="utf-8")

        # Extract summary
        lines = result.stdout.split("\n")
        summary_start = next((i for i, l in enumerate(lines) if "SUMMARY" in l), None)
        if summary_start:
            summary = "\n".join(lines[summary_start:summary_start+10])
            print(summary)
        else:
            print(f"  Completed in {elapsed:.0f}s")
            if result.returncode != 0:
                print(f"  EXIT CODE: {result.returncode}")
                print(f"  {result.stderr[:200]}" if result.stderr else "")

        return {"id": pid, "elapsed": round(elapsed, 1), "exit_code": result.returncode, "output_file": str(output_file)}

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  TIMED OUT after {elapsed:.0f}s")
        return {"id": pid, "elapsed": round(elapsed, 1), "exit_code": -1, "error": "timeout"}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"id": pid, "elapsed": 0, "exit_code": -1, "error": str(e)}


def main():
    print(f"Running {len(PROMPTS) - len(SKIP)} prompts (skipping {SKIP})")
    print(f"Results dir: {RESULTS_DIR}")

    all_results = []
    total_start = time.time()

    for prompt in PROMPTS:
        if prompt["id"] in SKIP:
            print(f"\n  Skipping prompt {prompt['id']} (already tested)")
            continue
        result = run_prompt(prompt)
        all_results.append(result)

    total_elapsed = time.time() - total_start

    print(f"\n{'='*60}")
    print(f"  ALL PROMPTS COMPLETE")
    print(f"  Total time: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    print(f"  Successful: {sum(1 for r in all_results if r['exit_code'] == 0)}/{len(all_results)}")
    print(f"{'='*60}")

    for r in all_results:
        status = "OK" if r["exit_code"] == 0 else "FAIL"
        print(f"  Prompt {r['id']:2d}: {status}  {r['elapsed']:6.1f}s  {r.get('output_file', r.get('error', ''))}")

    # Save summary
    summary_file = RESULTS_DIR / "summary.json"
    with open(summary_file, "w") as f:
        json.dump({"total_time_s": round(total_elapsed, 1), "results": all_results}, f, indent=2)
    print(f"\n  Summary: {summary_file}")


if __name__ == "__main__":
    main()
