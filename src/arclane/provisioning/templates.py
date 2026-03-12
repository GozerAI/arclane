"""Template registry — available app templates for new businesses."""

from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"


@dataclass
class Template:
    name: str
    slug: str
    description: str
    includes: list[str]


TEMPLATES: dict[str, Template] = {
    "landing-page": Template(
        name="Landing Page",
        slug="landing-page",
        description="Static site with contact form and analytics",
        includes=["index.html", "contact form", "analytics beacon"],
    ),
    "saas-app": Template(
        name="SaaS Application",
        slug="saas-app",
        description="Full-stack app with auth, dashboard, and API",
        includes=["auth", "dashboard", "REST API", "database"],
    ),
    "content-site": Template(
        name="Content Site",
        slug="content-site",
        description="Blog + newsletter signup + social integration",
        includes=["blog", "newsletter", "social links", "SEO"],
    ),
}


def list_templates() -> list[Template]:
    return list(TEMPLATES.values())


def get_template(slug: str) -> Template | None:
    return TEMPLATES.get(slug)
