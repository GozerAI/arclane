"""CLI test harness for Arclane cycle quality testing.

Uses the `claude` CLI (Claude Code) to generate real AI output for each
deliverable in the initial cycle. Routes through your Claude Max account.

Usage:
    # Test an existing business
    python tests/cli_test_cycle.py --existing \
        --url "https://www.mckinsey.com" \
        --description "Launching a boutique strategy consultancy for Series A-C SaaS startups" \
        --template landing-page

    # Test a new business
    python tests/cli_test_cycle.py --new \
        --description "A lightweight CRM for solo consultants and freelancers. $19/mo, no free tier." \
        --template saas-app

    # Use specific models
    python tests/cli_test_cycle.py --new \
        --description "AI meal planning for dietary restrictions" \
        --strategy-model opus --content-model sonnet --research-model haiku
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arclane.engine.executive_prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    EXECUTIVE_PROMPTS,
    phase_context_block,
)
from arclane.engine.intake import build_intake_brief

CLAUDE_CLI = r"C:\Users\chrisfromarose\.local\bin\claude.exe"

# Task definitions for the initial cycle (Day 1)
INITIAL_TASKS = {
    "strategy": {
        "area": "strategy",
        "title": "Mission and positioning brief",
        "complexity": "high",  # → opus or sonnet
    },
    "market_research": {
        "area": "market_research",
        "title": "Market research report",
        "complexity": "high",
    },
    "content": {
        "area": "content",
        "title": "Landing page / homepage draft",
        "complexity": "medium",  # → sonnet
    },
    "launch_tweet": {
        "area": "content",
        "title": "Launch announcement",
        "complexity": "low",  # → haiku or sonnet
    },
}

MODEL_MAP = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

DEFAULT_MODELS = {
    "high": "sonnet",
    "medium": "sonnet",
    "low": "haiku",
}


def build_prompt(
    task_key: str,
    business_name: str,
    description: str,
    website_url: str | None,
    website_summary: str | None,
    template: str,
) -> tuple[str, str]:
    """Build system + user prompt for a task."""
    task_def = INITIAL_TASKS[task_key]
    area = task_def["area"]

    prompt_pack = EXECUTIVE_PROMPTS.get(area, EXECUTIVE_PROMPTS["general"])

    system_prompt = "\n\n".join([
        ORCHESTRATOR_SYSTEM_PROMPT,
        prompt_pack["system_prompt"],
    ])

    # Phase context
    phase_block = phase_context_block(1, 1, None)

    # Intake brief
    intake_brief = build_intake_brief(
        description,
        website_summary=website_summary,
        website_url=website_url,
    )
    research_steps = "; ".join(intake_brief.get("instructions", []))

    # Task-specific descriptions
    task_descriptions = {
        "strategy": (
            f"Create the strategic operating brief for {business_name}. "
            f"Define the mission (1 sentence), core offer (what exactly the customer gets), "
            f"target customer (specific persona, not a demographic), wedge (why you vs. alternatives), "
            f"and top 3 launch priorities. Business context: {description}"
        ),
        "market_research": (
            f"Create a market research report for {business_name}. "
            f"Identify 3-5 specific competitors by name with their offers and pricing. "
            f"Find positioning gaps — where competitors are vague, slow, or overpriced. "
            f"Identify the #1 buyer objection and how to counter it. "
            f"End with 3 concrete opportunities to exploit. Business context: {description}"
        ),
        "content": (
            f"Write the landing page copy for {business_name}. "
            f"Structure: (1) Hero headline (under 10 words, outcome-focused) + subheadline + CTA button text. "
            f"(2) Problem section: name the pain. (3) Solution: 3 bullet proof points. "
            f"(4) Social proof section (framework even if no testimonials yet). "
            f"(5) Objection handling: top 3 objections with one-line responses. "
            f"(6) Final CTA. Write ALL actual copy — no placeholders. Business context: {description}"
        ),
        "launch_tweet": (
            f"Write a launch announcement for {business_name} that works on Twitter/X. "
            f"Under 280 characters. Hook in the first line. Clear value prop. "
            f"End with a CTA (link placeholder is fine). Business context: {description}"
        ),
    }

    context_suffix = ""
    if website_summary:
        context_suffix = f"\n\nExisting website analysis:\n{website_summary}"
    elif website_url:
        context_suffix = f"\n\nExisting website: {website_url}"

    user_prompt = (
        f"Business name: {business_name}\n"
        f"Task: {task_def['title']}\n"
        f"Executive lens: {prompt_pack['executive']}\n"
        f"\n{phase_block}\n"
        f"\nBusiness brief: {description}{context_suffix}\n"
        f"\nIntake checklist: {research_steps}\n"
        f"\nRequested deliverable:\n{task_descriptions[task_key]}\n"
        f"\nReturn the deliverable in plain language. No agent jargon. "
        f"No placeholders like [insert X]. Use the actual business name and details."
    )

    return system_prompt, user_prompt


def call_claude(
    system_prompt: str,
    user_prompt: str,
    model: str = "sonnet",
    max_tokens: int = 2000,
) -> tuple[str, float]:
    """Call Claude CLI and return (output, elapsed_seconds)."""
    model_id = MODEL_MAP.get(model, model)

    # Combine into a single prompt for CLI (system as prefix)
    full_prompt = f"<system>\n{system_prompt}\n</system>\n\n{user_prompt}"

    start = time.time()
    try:
        result = subprocess.run(
            [
                CLAUDE_CLI,
                "-p", full_prompt,
                "--model", model_id,
                "--output-format", "text",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        elapsed = time.time() - start

        if result.returncode != 0:
            return f"[ERROR: {result.stderr[:300]}]", elapsed

        return result.stdout.strip(), elapsed

    except subprocess.TimeoutExpired:
        return "[ERROR: Timed out after 120s]", time.time() - start
    except Exception as e:
        return f"[ERROR: {e}]", time.time() - start


def run_test(args):
    """Run a full initial cycle test."""
    description = args.description
    website_url = getattr(args, "url", None)
    template = args.template or "content-site"
    business_type = "existing" if args.existing else "new"

    # Auto-generate business name
    words = [w for w in description.split()[:3] if len(w) > 2]
    business_name = " ".join(w.capitalize() for w in words) or "Test Business"

    print("=" * 70)
    print(f"  ARCLANE CYCLE TEST — {business_type.upper()} BUSINESS")
    print(f"  Name: {business_name}")
    print(f"  Template: {template}")
    if website_url:
        print(f"  Website: {website_url}")
    print(f"  Description: {description[:80]}...")
    print("=" * 70)

    # Determine models per task
    model_overrides = {}
    if args.strategy_model:
        model_overrides["strategy"] = args.strategy_model
    if args.content_model:
        model_overrides["content"] = args.content_model
        model_overrides["launch_tweet"] = args.content_model
    if args.research_model:
        model_overrides["market_research"] = args.research_model

    # Website summary (stub for now — real version would fetch)
    website_summary = None
    if website_url:
        website_summary = f"Existing business website at {website_url}. Analyze their current positioning and recommend improvements."

    total_time = 0
    results = {}

    for task_key, task_def in INITIAL_TASKS.items():
        complexity = task_def["complexity"]
        model = model_overrides.get(task_key, DEFAULT_MODELS[complexity])

        print(f"\n{'-' * 70}")
        print(f"  Task: {task_def['title']}")
        print(f"  Model: {model} ({MODEL_MAP.get(model, model)})")
        print(f"  Complexity: {complexity}")
        print(f"{'-' * 70}")

        system_prompt, user_prompt = build_prompt(
            task_key, business_name, description,
            website_url, website_summary, template,
        )

        # Show prompt stats
        sys_tokens = len(system_prompt.split())
        user_tokens = len(user_prompt.split())
        print(f"  Prompt: ~{sys_tokens} system words + ~{user_tokens} user words")
        print(f"  Generating...", end="", flush=True)

        output, elapsed = call_claude(system_prompt, user_prompt, model=model)
        total_time += elapsed
        output_words = len(output.split())

        print(f" done ({elapsed:.1f}s, ~{output_words} words)")
        print()

        # Print the output
        for line in output.split("\n"):
            print(f"  {line}")

        results[task_key] = {
            "title": task_def["title"],
            "model": model,
            "elapsed_s": round(elapsed, 1),
            "output_words": output_words,
            "output_chars": len(output),
        }

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Total generation time: {total_time:.1f}s")
    print(f"  Deliverables: {len(results)}")
    print()
    for key, r in results.items():
        print(f"  {r['title']:40} {r['model']:8} {r['elapsed_s']:6.1f}s  {r['output_words']:5} words")
    print()

    # Save results
    output_path = Path(__file__).parent / f"cycle_test_{int(time.time())}.json"
    with open(output_path, "w") as f:
        json.dump({
            "business_name": business_name,
            "business_type": business_type,
            "description": description,
            "website_url": website_url,
            "template": template,
            "total_time_s": round(total_time, 1),
            "results": results,
        }, f, indent=2)
    print(f"  Results saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Test Arclane cycle output quality")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--existing", action="store_true", help="Existing business with website")
    group.add_argument("--new", action="store_true", help="New business (no website)")

    parser.add_argument("--description", "-d", required=True, help="Business description")
    parser.add_argument("--url", "-u", help="Website URL (existing businesses)")
    parser.add_argument("--template", "-t", choices=["content-site", "saas-app", "landing-page"],
                       default="content-site", help="Template type")

    # Model overrides
    parser.add_argument("--strategy-model", choices=["opus", "sonnet", "haiku"])
    parser.add_argument("--content-model", choices=["opus", "sonnet", "haiku"])
    parser.add_argument("--research-model", choices=["opus", "sonnet", "haiku"])

    args = parser.parse_args()

    if args.existing and not args.url:
        parser.error("--existing requires --url")

    run_test(args)


if __name__ == "__main__":
    main()
