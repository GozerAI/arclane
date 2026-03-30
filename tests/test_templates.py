"""Test template registry."""

from arclane.provisioning.templates import get_template, list_templates


def test_list_templates():
    templates = list_templates()
    assert len(templates) == 3
    slugs = {t.slug for t in templates}
    assert "landing-page" in slugs
    assert "saas-app" in slugs
    assert "content-site" in slugs


def test_get_template():
    t = get_template("saas-app")
    assert t is not None
    assert t.name == "SaaS Application"
    assert "auth" in t.includes


def test_get_template_unknown():
    assert get_template("nonexistent") is None
