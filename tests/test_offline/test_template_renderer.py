"""Tests for offline template rendering (item 758)."""

import json
from pathlib import Path

import pytest

from arclane.offline.template_renderer import (
    OfflineTemplateRenderer,
    RenderedTemplate,
    TemplateContext,
    BUILTIN_TEMPLATES,
)


@pytest.fixture
def renderer():
    return OfflineTemplateRenderer()


@pytest.fixture
def context():
    return TemplateContext(
        business_name="Acme Corp",
        slug="acme-corp",
        description="We sell widgets",
        contact_email="hello@acme.com",
        tagline="Widgets done right",
    )


class TestTemplateContext:
    def test_to_vars(self):
        ctx = TemplateContext(business_name="Test", slug="test")
        v = ctx.to_vars()
        assert v["business_name"] == "Test"
        assert v["slug"] == "test"

    def test_default_description(self):
        ctx = TemplateContext(business_name="Foo", slug="foo")
        v = ctx.to_vars()
        assert "Foo" in v["description"]

    def test_extra_vars(self):
        ctx = TemplateContext(business_name="X", slug="x", extra={"cta_text": "Buy Now"})
        v = ctx.to_vars()
        assert v["cta_text"] == "Buy Now"


class TestRenderedTemplate:
    def test_file_count(self):
        rt = RenderedTemplate(template_name="test", files={"a.html": "<h1>hi</h1>", "b.css": "body{}"}, context={})
        assert rt.file_count == 2

    def test_to_dict(self):
        rt = RenderedTemplate(template_name="test", files={"a.html": ""}, context={})
        d = rt.to_dict()
        assert d["template_name"] == "test"
        assert "files" in d

    def test_write_to(self, tmp_path):
        rt = RenderedTemplate(
            template_name="test",
            files={"index.html": "<h1>Hello</h1>", "styles.css": "body{}"},
            context={},
        )
        written = rt.write_to(tmp_path / "output")
        assert len(written) == 2
        assert (tmp_path / "output" / "index.html").exists()
        assert (tmp_path / "output" / "index.html").read_text() == "<h1>Hello</h1>"


class TestOfflineTemplateRenderer:
    def test_available_templates(self, renderer):
        templates = renderer.available_templates
        assert "content-site" in templates
        assert "saas-app" in templates
        assert "landing-page" in templates

    def test_render_content_site(self, renderer, context):
        result = renderer.render("content-site", context)
        assert result.template_name == "content-site"
        assert "Acme Corp" in result.files["index.html"]
        assert result.file_count >= 2

    def test_render_saas_app(self, renderer, context):
        result = renderer.render("saas-app", context)
        assert "Acme Corp" in result.files["index.html"]
        assert "login.html" in result.files

    def test_render_landing_page(self, renderer, context):
        result = renderer.render("landing-page", context)
        assert "Acme Corp" in result.files["index.html"]
        assert "Widgets done right" in result.files["index.html"]

    def test_render_unknown_template(self, renderer, context):
        with pytest.raises(ValueError, match="not found"):
            renderer.render("nonexistent", context)

    def test_render_config_json(self, renderer, context):
        result = renderer.render("content-site", context)
        cfg = json.loads(result.files["config.json"])
        assert cfg["name"] == "Acme Corp"
        assert cfg["slug"] == "acme-corp"

    def test_missing_required_var_warning(self, renderer):
        ctx = TemplateContext(business_name="", slug="")
        result = renderer.render("content-site", ctx)
        assert any("missing_required_var" in w for w in result.warnings)

    def test_render_to_dir(self, renderer, context, tmp_path):
        result = renderer.render_to_dir("content-site", context, tmp_path / "out")
        assert (tmp_path / "out" / "index.html").exists()
        assert "Acme Corp" in (tmp_path / "out" / "index.html").read_text()

    def test_register_custom_template(self, renderer, context):
        renderer.register_template(
            "custom",
            files={"page.html": "<h1>{{business_name}}</h1>"},
            required_vars=["business_name"],
        )
        assert "custom" in renderer.available_templates
        result = renderer.render("custom", context)
        assert "Acme Corp" in result.files["page.html"]

    def test_preview(self, renderer, context):
        files = renderer.preview("landing-page", context)
        assert isinstance(files, dict)
        assert "index.html" in files
        assert "Acme Corp" in files["index.html"]

    def test_get_template_info(self, renderer):
        info = renderer.get_template_info("content-site")
        assert info is not None
        assert info["name"] == "Content Site"
        assert "index.html" in info["files"]

    def test_get_template_info_missing(self, renderer):
        assert renderer.get_template_info("nope") is None

    def test_filesystem_template(self, tmp_path):
        tpl_dir = tmp_path / "templates" / "my-site"
        tpl_dir.mkdir(parents=True)
        (tpl_dir / "index.html").write_text("<h1>{{business_name}}</h1>")
        (tpl_dir / "template.json").write_text('{"name": "My Site", "required_vars": ["business_name"]}')

        renderer = OfflineTemplateRenderer(templates_dir=tmp_path / "templates")
        assert "my-site" in renderer.available_templates
        result = renderer.render("my-site", TemplateContext(business_name="Test", slug="test"))
        assert "Test" in result.files["index.html"]

    def test_interpolation_missing_vars(self, renderer):
        renderer.register_template("gaps", files={"out.txt": "Hello {{name}} from {{city}}"})
        ctx = TemplateContext(extra={"name": "Alice"})
        result = renderer.render("gaps", ctx)
        assert "Alice" in result.files["out.txt"]
        # Missing {{city}} becomes empty
        assert "from " in result.files["out.txt"]
