"""Quick smoke test: verify content injection using real prompt output.

Reads the landing-page copy from prompt_16.txt (Fractional CTO),
runs it through the content injector, and writes a preview HTML file.

Usage:
    python tests/test_injection.py
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arclane.provisioning.content_injector import _parse_landing_page, _apply_variables

PROMPT_FILE = Path(__file__).parent / "prompt_results" / "prompt_16.txt"
TEMPLATE_FILE = Path(__file__).parent.parent / "templates" / "landing-page" / "index.html"
OUT_FILE = Path(__file__).parent / "injection_preview.html"


def extract_landing_copy(text: str) -> str:
    """Pull the landing page section from the test harness output."""
    # Find the "Landing page / homepage draft" task output
    marker = "Task: Landing page / homepage draft"
    start = text.find(marker)
    if start == -1:
        raise ValueError("Landing page task not found in output")

    # Skip the header lines, find the actual content (after "Generating... done...")
    content_start = text.find("Generating...", start)
    content_start = text.find("\n", content_start) + 1  # after the "done" line
    content_start = text.find("\n", content_start) + 1  # skip blank line

    # Find the next task section or summary
    next_task = text.find("----------------------------------------------------------------------", content_start)
    if next_task == -1:
        next_task = text.find("SUMMARY", content_start)

    raw = text[content_start:next_task].strip()

    # Remove the 2-space indent the test harness adds
    lines = raw.split("\n")
    cleaned = []
    for line in lines:
        if line.startswith("  "):
            cleaned.append(line[2:])
        else:
            cleaned.append(line)

    return "\n".join(cleaned)


class FakeBusiness:
    name = "Fractional CTO"
    slug = "fractional-cto"
    description = "Fractional CTO service for non-technical seed-stage founders"


def main():
    if not PROMPT_FILE.exists():
        print(f"ERROR: {PROMPT_FILE} not found. Run run_all_prompts.py first.")
        sys.exit(1)

    raw = PROMPT_FILE.read_text(encoding="utf-8", errors="replace")
    landing_copy = extract_landing_copy(raw)

    print(f"Extracted {len(landing_copy)} chars of landing page copy")
    print(f"First 200 chars:\n{landing_copy[:200]}\n")

    business = FakeBusiness()
    variables = _parse_landing_page(landing_copy, business)

    print("Parsed variables:")
    for k, v in variables.items():
        preview = (v[:80] + "...") if len(v) > 80 else v
        print(f"  {k:30} = {preview!r}")

    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    rendered = _apply_variables(template, variables)

    # Check for leftover placeholders
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", rendered)
    if leftover:
        print(f"\nWARNING: Unfilled placeholders: {leftover}")
    else:
        print("\nAll placeholders filled.")

    OUT_FILE.write_text(rendered, encoding="utf-8")
    print(f"\nPreview written to: {OUT_FILE}")
    print("Open in browser to verify layout.")


if __name__ == "__main__":
    main()
