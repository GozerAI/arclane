"""Content injector — pushes AI-generated content into deployed tenant templates.

After the initial cycle produces a landing page draft, strategy brief, and
market research, this module parses the structured content and injects it
into the template HTML so the subdomain shows real, AI-written copy instead
of placeholder text.
"""

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.config import settings
from arclane.core.logging import get_logger
from arclane.models.tables import Business, Content

log = get_logger("provisioning.content_injector")

WORKSPACES_DIR = Path(settings.workspaces_root)


async def inject_landing_page(business: Business, session: AsyncSession) -> bool:
    """Find the AI-generated landing page content and inject it into the deployed template.

    Returns True if injection succeeded.
    """
    # Find the best content to inject — prefer landing page, fall back to strategy brief
    result = await session.execute(
        select(Content).where(
            Content.business_id == business.id,
            Content.content_type.in_(["blog", "report"]),
        ).order_by(Content.created_at.asc())
    )
    all_content = result.scalars().all()

    # Prefer blog (landing page draft) over report (strategy brief)
    landing_content = next(
        (c for c in all_content if c.content_type == "blog" and c.body),
        next((c for c in all_content if c.body), None),
    )
    if not landing_content:
        log.info("No injectable content found for %s — skipping injection", business.slug)
        return False

    # Parse the AI-generated content into template variables
    variables = _parse_landing_page(landing_content.body, business)

    # Apply to the workspace template
    workspace = WORKSPACES_DIR / business.slug
    index_path = workspace / "index.html"

    if not index_path.exists():
        log.warning("Template not found at %s — skipping injection", index_path)
        return False

    template = index_path.read_text(encoding="utf-8")
    rendered = _apply_variables(template, variables)
    index_path.write_text(rendered, encoding="utf-8")

    log.info("Landing page content injected for %s (%d chars)", business.slug, len(rendered))
    return True


def _parse_landing_page(body: str, business: Business) -> dict:
    """Parse AI-generated landing page copy into template variables.

    Handles both structured markdown (with ## headers) and freeform text.
    """
    vars = {
        "BUSINESS_NAME": business.name,
        "BUSINESS_SLUG": business.slug,
        "HEADLINE": business.name,
        "SUBHEADLINE": (business.description or "")[:200],
        "CTA_TEXT": "Get Started",
        "PROBLEM_HEADING": "The Problem",
        "PROBLEM_BODY": "",
        "SOLUTION_HEADING": "The Solution",
        "PROOF_POINTS": "",
        "PROOF_HEADING": "Why Us",
        "PROOF_BODY": "",
        "OBJECTIONS_HEADING": "Common Questions",
        "OBJECTIONS_BODY": "",
        "FINAL_CTA_HEADING": "Ready to Start?",
        "FINAL_CTA_BODY": "",
    }

    sections = _split_sections(body)

    # Extract hero
    hero = sections.get("hero", {})
    if hero.get("heading"):
        vars["HEADLINE"] = hero["heading"]
    if hero.get("subheadline"):
        vars["SUBHEADLINE"] = hero["subheadline"]
    if hero.get("cta"):
        vars["CTA_TEXT"] = hero["cta"]

    # Extract problem
    problem = sections.get("problem", {})
    if problem.get("heading"):
        vars["PROBLEM_HEADING"] = problem["heading"]
    if problem.get("body"):
        vars["PROBLEM_BODY"] = problem["body"]

    # Extract solution / proof points
    solution = sections.get("solution", {})
    if solution.get("heading"):
        vars["SOLUTION_HEADING"] = solution["heading"]
    if solution.get("points"):
        vars["PROOF_POINTS"] = "\n".join(
            f'<li><strong>{p["title"]}</strong> {p["body"]}</li>'
            for p in solution["points"]
        )
    elif solution.get("body"):
        vars["PROOF_POINTS"] = f"<li>{solution['body']}</li>"

    # Extract social proof
    proof = sections.get("proof", {})
    if proof.get("heading"):
        vars["PROOF_HEADING"] = proof["heading"]
    if proof.get("body"):
        vars["PROOF_BODY"] = proof["body"]

    # Extract objections
    objections = sections.get("objections", {})
    if objections.get("heading"):
        vars["OBJECTIONS_HEADING"] = objections["heading"]
    if objections.get("items"):
        vars["OBJECTIONS_BODY"] = "\n".join(
            f'<div class="objection"><strong>{o["question"]}</strong><p>{o["answer"]}</p></div>'
            for o in objections["items"]
        )
    elif objections.get("body"):
        vars["OBJECTIONS_BODY"] = f"<p>{objections['body']}</p>"

    # Extract final CTA
    final = sections.get("final_cta", {})
    if final.get("heading"):
        vars["FINAL_CTA_HEADING"] = final["heading"]
    if final.get("body"):
        vars["FINAL_CTA_BODY"] = final["body"]

    return vars


def _split_sections(body: str) -> dict:
    """Split markdown-structured content into named sections.

    Recognizes patterns like:
    ## (1) Hero Section / ## Hero / **Headline:**
    ## (2) Problem Section / ## Problem / ## The Challenge
    ## (3) Solution / ## Proof Points / ## What we do
    ## (4) Social Proof
    ## (5) Objection Handling / ## FAQ / ## Straight answers
    ## (6) Final CTA
    """
    sections: dict = {}
    lines = body.split("\n")

    current_section = None
    current_lines: list[str] = []

    section_patterns = {
        "hero": re.compile(r"(?:hero|headline|header|\(1\))", re.IGNORECASE),
        "problem": re.compile(r"(?:problem|challenge|pain|\(2\))", re.IGNORECASE),
        "solution": re.compile(r"(?:solution|proof.?point|what.?we|everything.?you|\(3\))", re.IGNORECASE),
        "proof": re.compile(r"(?:social.?proof|testimonial|trust|built.?for|\(4\))", re.IGNORECASE),
        "objections": re.compile(r"(?:objection|faq|question|straight.?answer|\(5\))", re.IGNORECASE),
        "final_cta": re.compile(r"(?:final.?cta|ready|get.?started|pipeline|\(6\))", re.IGNORECASE),
    }

    for line in lines:
        stripped = line.strip()

        # Check for section headers (## or ** bold headers).
        # Exclude bold label lines like **Headline:** or **CTA Button:** — the
        # bold-wrapped text ends with ":** " which means the inner text has a
        # trailing colon; those are field labels, not section headings.
        inner = stripped.strip("*").strip()
        is_bold_label = (
            stripped.startswith("**") and stripped.endswith("**")
            and inner.endswith(":")
        )
        is_header = stripped.startswith("## ") or (
            not is_bold_label
            and stripped.startswith("**")
            and stripped.endswith("**")
        )
        if is_header:
            # Save previous section
            if current_section:
                sections[current_section] = _parse_section_content(current_section, current_lines)

            # Detect new section
            header_text = stripped.lstrip("#").strip().strip("*").strip()
            matched = False
            for section_name, pattern in section_patterns.items():
                if pattern.search(header_text):
                    current_section = section_name
                    current_lines = []
                    matched = True
                    # Extract heading from the header
                    current_lines.append(f"_heading_:{header_text}")
                    break

            if not matched:
                # Unknown section — keep accumulating into current
                current_lines.append(stripped)
        else:
            current_lines.append(stripped)

    # Save last section
    if current_section:
        sections[current_section] = _parse_section_content(current_section, current_lines)

    return sections


def _parse_section_content(section_name: str, lines: list[str]) -> dict:
    """Parse lines within a section into structured data.

    Handles both inline format (**Headline:** value) and two-line format
    where the label is on one line and the value is on the next:
        **Headline:**
        Your headline text here
    """
    result: dict = {}
    body_lines = []
    heading = None
    points = []
    pending_label = None  # "heading" | "subheadline" | "cta" | "question"

    for line in lines:
        stripped = line.strip()

        if line.startswith("_heading_:"):
            heading = line[len("_heading_:"):]
            continue
        if not stripped:
            continue

        # If we're waiting for a value on the next non-empty line
        if pending_label:
            value = stripped.lstrip("→➔>-").strip()
            if pending_label == "heading" and not result.get("heading"):
                result["heading"] = value
            elif pending_label == "subheadline":
                result["subheadline"] = value
            elif pending_label == "cta":
                result["cta"] = value
            elif pending_label == "question":
                items = result.setdefault("items", [])
                items.append({"question": result.pop("_pending_question", ""), "answer": value})
            pending_label = None
            continue

        def _extract_inline(l: str) -> str:
            """Pull the value from a label line: **Label:** value → value."""
            text = re.sub(r"^\*+", "", l).rstrip("*").strip()
            if ":" in text:
                text = text.split(":", 1)[1].strip()
            # Strip any remaining bold markers left over from inline bold closure
            text = re.sub(r"^\*+\s*", "", text).strip()
            return text

        # Headline / header — inline or label-only
        if stripped.startswith("**Headline") or stripped.startswith("**Header"):
            value = _extract_inline(stripped)
            if value and not result.get("heading"):
                result["heading"] = value
            elif not value:
                pending_label = "heading"
            continue

        # Subheadline
        if stripped.startswith("**Subheadline") or stripped.startswith("**Sub-headline"):
            value = _extract_inline(stripped)
            result["subheadline"] = value if value else None
            if not value:
                pending_label = "subheadline"
            continue

        # CTA button
        if stripped.startswith("**CTA") or stripped.startswith("**Button"):
            value = _extract_inline(stripped)
            result["cta"] = value if value else None
            if not value:
                pending_label = "cta"
            continue

        # Arrow-style proof points: **→ Title** or **→ Title** (various arrow chars)
        # Also matches mojibake â†' (UTF-8 → misread as cp1252) for robustness
        arrow_match = re.match(
            r"\*\*(?:[→➔►➜➤\u2192\u279C\u27A4]|â†['\u2019])\s*(.+?)\*\*$",
            stripped,
        ) or re.match(r"\*\*(?:[→➔►➜➤\u2192\u279C\u27A4]|â†['\u2019])\s*(.+)$", stripped)
        if arrow_match:
            title = arrow_match.group(1).strip().rstrip("*").strip()
            points.append({"title": title, "body": ""})
            continue

        # Body text that follows an arrow point (augments last point)
        if points and points[-1].get("body") == "" and not stripped.startswith("**"):
            points[-1]["body"] = stripped
            continue

        # Dash-bold proof point: - **Title.** body
        if stripped.startswith("- **") and "**" in stripped[4:]:
            match = re.match(r"-\s*\*\*(.+?)\*\*\.?\s*(.*)", stripped)
            if match:
                points.append({"title": match.group(1).strip(), "body": match.group(2).strip()})
                continue

        # Q&A objection: **"question text"** or **"question"**
        if re.match(r'\*\*["\u201c\u201d]', stripped):
            question = re.sub(r'^\*+|["\u201c\u201d\*]+$', "", stripped).strip()
            result["_pending_question"] = question
            pending_label = "question"
            continue

        # Answer after a pending question (fallback)
        if result.get("_pending_question"):
            items = result.setdefault("items", [])
            items.append({"question": result.pop("_pending_question"), "answer": stripped})
            continue

        # Quoted lines (testimonials / social proof)
        if stripped.startswith("> "):
            text = stripped[2:].strip()
            if not result.get("heading"):
                result["heading"] = text
            else:
                body_lines.append(stripped)
            continue

        body_lines.append(stripped)

    if not result.get("heading") and heading:
        result["heading"] = heading
    if body_lines:
        result["body"] = "\n".join(body_lines)
    if points:
        result["points"] = points

    result.pop("_pending_question", None)

    return result


def _apply_variables(template: str, variables: dict) -> str:
    """Replace {{VARIABLE}} placeholders in template HTML."""
    result = template
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value))
    return result
