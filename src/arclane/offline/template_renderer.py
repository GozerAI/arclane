"""Offline template rendering without external service dependencies.

Item 758: Renders business templates (content-site, saas-app, landing-page)
using only local filesystem assets and string interpolation. No network calls,
no Docker API, no LLM needed.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Built-in template definitions (used when template directory is unavailable)
BUILTIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "content-site": {
        "name": "Content Site",
        "description": "Blog and content publishing site with Express.js",
        "schema_version": 1,
        "files": {
            "index.html": "<!DOCTYPE html>\n<html><head><title>{{business_name}}</title></head>\n<body><h1>{{business_name}}</h1><p>{{description}}</p></body></html>",
            "styles.css": "body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; }",
            "config.json": '{"name": "{{business_name}}", "slug": "{{slug}}", "template": "content-site"}',
        },
        "required_vars": ["business_name", "slug"],
        "optional_vars": ["description", "contact_email", "tagline"],
    },
    "saas-app": {
        "name": "SaaS Application",
        "description": "SaaS application with authentication via Express.js",
        "schema_version": 1,
        "files": {
            "index.html": "<!DOCTYPE html>\n<html><head><title>{{business_name}} - Dashboard</title></head>\n<body><h1>{{business_name}}</h1><div id='app'></div></body></html>",
            "login.html": "<!DOCTYPE html>\n<html><head><title>Login - {{business_name}}</title></head>\n<body><h1>Sign In</h1><form><input placeholder='Email'><input type='password' placeholder='Password'><button>Login</button></form></body></html>",
            "config.json": '{"name": "{{business_name}}", "slug": "{{slug}}", "template": "saas-app", "auth_enabled": true}',
        },
        "required_vars": ["business_name", "slug"],
        "optional_vars": ["description", "contact_email", "pricing_url"],
    },
    "landing-page": {
        "name": "Landing Page",
        "description": "Static marketing landing page",
        "schema_version": 1,
        "files": {
            "index.html": "<!DOCTYPE html>\n<html><head><title>{{business_name}}</title></head>\n<body><header><h1>{{business_name}}</h1><p>{{tagline}}</p></header><section><p>{{description}}</p></section></body></html>",
            "styles.css": "body { margin: 0; font-family: system-ui; } header { background: #1a1a2e; color: white; padding: 4rem 2rem; text-align: center; }",
            "config.json": '{"name": "{{business_name}}", "slug": "{{slug}}", "template": "landing-page"}',
        },
        "required_vars": ["business_name", "slug"],
        "optional_vars": ["description", "tagline", "cta_text", "contact_email"],
    },
}

# Variable pattern: {{variable_name}}
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


@dataclass
class TemplateContext:
    """Variables available for template interpolation."""

    business_name: str = ""
    slug: str = ""
    description: str = ""
    contact_email: str = ""
    tagline: str = ""
    website_url: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def to_vars(self) -> Dict[str, str]:
        """Flatten to a variable dictionary for interpolation."""
        base = {
            "business_name": self.business_name,
            "slug": self.slug,
            "description": self.description or f"Welcome to {self.business_name}",
            "contact_email": self.contact_email,
            "tagline": self.tagline or self.description[:80] if self.description else "",
            "website_url": self.website_url,
        }
        base.update(self.extra)
        return base


@dataclass
class RenderedTemplate:
    """Result of an offline template render."""

    template_name: str
    files: Dict[str, str]  # filename -> rendered content
    context: Dict[str, str]
    rendered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: List[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_name": self.template_name,
            "file_count": self.file_count,
            "files": list(self.files.keys()),
            "rendered_at": self.rendered_at.isoformat(),
            "warnings": self.warnings,
        }

    def write_to(self, output_dir: Path) -> List[Path]:
        """Write rendered files to a directory. Returns list of written paths."""
        output_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for filename, content in self.files.items():
            path = output_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        return written


class OfflineTemplateRenderer:
    """Renders business templates entirely offline using local assets.

    Uses built-in templates as fallback when template directories are missing.
    All rendering is pure string interpolation -- no network, no Docker, no LLM.
    """

    def __init__(self, templates_dir: Optional[Path] = None):
        self._templates_dir = templates_dir
        self._custom_templates: Dict[str, Dict[str, Any]] = {}

    @property
    def available_templates(self) -> List[str]:
        """List all available template names."""
        names = set(BUILTIN_TEMPLATES.keys())
        names.update(self._custom_templates.keys())
        if self._templates_dir and self._templates_dir.exists():
            for d in self._templates_dir.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    names.add(d.name)
        return sorted(names)

    def register_template(
        self,
        name: str,
        files: Dict[str, str],
        required_vars: Optional[List[str]] = None,
        optional_vars: Optional[List[str]] = None,
        schema_version: int = 1,
    ) -> None:
        """Register a custom template definition."""
        self._custom_templates[name] = {
            "name": name,
            "files": files,
            "required_vars": required_vars or [],
            "optional_vars": optional_vars or [],
            "schema_version": schema_version,
        }

    def render(
        self,
        template_name: str,
        context: TemplateContext,
    ) -> RenderedTemplate:
        """Render a template with the given context.

        Raises ValueError if the template is not found.
        """
        template_def = self._resolve_template(template_name)
        if template_def is None:
            raise ValueError(f"Template '{template_name}' not found")

        variables = context.to_vars()
        warnings: List[str] = []

        # Check required vars
        for var in template_def.get("required_vars", []):
            if not variables.get(var):
                warnings.append(f"missing_required_var:{var}")

        # Render each file
        rendered_files: Dict[str, str] = {}
        for filename, content_template in template_def.get("files", {}).items():
            rendered = self._interpolate(content_template, variables)
            rendered_files[filename] = rendered

        return RenderedTemplate(
            template_name=template_name,
            files=rendered_files,
            context=variables,
            warnings=warnings,
        )

    def render_to_dir(
        self,
        template_name: str,
        context: TemplateContext,
        output_dir: Path,
    ) -> RenderedTemplate:
        """Render and write files to output directory."""
        result = self.render(template_name, context)
        result.write_to(output_dir)
        return result

    def get_template_info(self, template_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata about a template."""
        tpl = self._resolve_template(template_name)
        if tpl is None:
            return None
        return {
            "name": tpl.get("name", template_name),
            "description": tpl.get("description", ""),
            "schema_version": tpl.get("schema_version", 1),
            "required_vars": tpl.get("required_vars", []),
            "optional_vars": tpl.get("optional_vars", []),
            "file_count": len(tpl.get("files", {})),
            "files": list(tpl.get("files", {}).keys()),
        }

    def preview(self, template_name: str, context: TemplateContext) -> Dict[str, str]:
        """Preview rendered content without writing to disk."""
        result = self.render(template_name, context)
        return result.files

    def _resolve_template(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve template by name: custom > filesystem > builtin."""
        if name in self._custom_templates:
            return self._custom_templates[name]

        # Check filesystem templates
        if self._templates_dir:
            tpl_dir = self._templates_dir / name
            if tpl_dir.is_dir():
                return self._load_filesystem_template(tpl_dir, name)

        if name in BUILTIN_TEMPLATES:
            return BUILTIN_TEMPLATES[name]

        return None

    def _load_filesystem_template(self, tpl_dir: Path, name: str) -> Dict[str, Any]:
        """Load a template from a directory on disk."""
        files: Dict[str, str] = {}
        meta: Dict[str, Any] = {"name": name, "schema_version": 1}

        # Load metadata if present
        meta_path = tpl_dir / "template.json"
        if meta_path.exists():
            try:
                meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to parse template.json for %s", name)

        # Load all non-meta files
        for path in tpl_dir.rglob("*"):
            if path.is_file() and path.name != "template.json":
                rel = path.relative_to(tpl_dir).as_posix()
                try:
                    files[rel] = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    logger.warning("Skipping unreadable file: %s", rel)

        meta["files"] = files
        return meta

    @staticmethod
    def _interpolate(template: str, variables: Dict[str, str]) -> str:
        """Replace {{var}} placeholders with values. Missing vars become empty string."""
        def _replace(match: re.Match) -> str:
            key = match.group(1)
            return variables.get(key, "")
        return _VAR_PATTERN.sub(_replace, template)
