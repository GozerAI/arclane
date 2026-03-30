"""Landing page renderer — builds unique, styled websites from structured content.

Takes a design spec (colors, fonts, layout) and section content (hero, features,
pricing, etc.) and produces a polished single-page HTML site tailored to the
product type.
"""

import json
from html import escape

from arclane.core.logging import get_logger

log = get_logger("page_renderer")

# Font stacks by style keyword
_FONTS = {
    "modern": ("'Inter', 'Segoe UI', system-ui, sans-serif", "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"),
    "classic": ("'Merriweather', Georgia, serif", "https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700&family=Open+Sans:wght@400;600&display=swap"),
    "playful": ("'Nunito', 'Quicksand', sans-serif", "https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap"),
    "technical": ("'JetBrains Mono', 'Space Grotesk', monospace", "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"),
    "elegant": ("'Playfair Display', 'Cormorant Garamond', serif", "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Lato:wght@400;700&display=swap"),
}

_SECTION_RENDERERS: dict[str, "callable"] = {}

_CHECKOUT_SCRIPT = """
// Pricing CTAs: redirect to Stripe Checkout
document.querySelectorAll('.pricing-cta').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        var plan = this.dataset.plan;
        var priceStr = this.dataset.amount || '0';
        // Parse dollar amount to cents: "$9.99/month" -> 999
        var match = priceStr.match(/[\\d.]+/);
        var cents = match ? Math.round(parseFloat(match[0]) * 100) : 0;
        if (cents < 100) {
            // Free plan or unparseable — fall back to signup
            document.getElementById('signup-modal').style.display = 'flex';
            return;
        }
        this.textContent = 'Redirecting...';
        this.style.pointerEvents = 'none';
        fetch('/checkout', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({plan: plan, amount_cents: cents, type: 'sale'})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.checkout_url) {
                window.location.href = data.checkout_url;
            } else {
                alert(data.error || 'Checkout unavailable');
                btn.textContent = btn.dataset.originalText || 'Get Started';
                btn.style.pointerEvents = '';
            }
        })
        .catch(function() {
            alert('Connection error. Please try again.');
            btn.textContent = btn.dataset.originalText || 'Get Started';
            btn.style.pointerEvents = '';
        });
    });
});
"""


def _renderer(section_type: str):
    def decorator(fn):
        _SECTION_RENDERERS[section_type] = fn
        return fn
    return decorator


def _e(text) -> str:
    """Escape HTML."""
    return escape(str(text)) if text else ""


def render_landing_page(business_name: str, content_body: str, *, has_stripe: bool = False) -> str | None:
    """Parse structured JSON from content body and render a full HTML page.

    Args:
        has_stripe: If True, pricing CTAs use Stripe Checkout instead of signup form.

    Returns None if the content isn't valid structured JSON (falls back to
    the card-based renderer).
    """
    try:
        data = json.loads(content_body)
    except (json.JSONDecodeError, TypeError):
        # Try extracting JSON from markdown code block
        if "```json" in (content_body or ""):
            start = content_body.index("```json") + 7
            end = content_body.index("```", start)
            try:
                data = json.loads(content_body[start:end])
            except (json.JSONDecodeError, ValueError):
                return None
        elif "```" in (content_body or ""):
            start = content_body.index("```") + 3
            end = content_body.index("```", start)
            try:
                data = json.loads(content_body[start:end])
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            return None

    if not isinstance(data, dict) or "sections" not in data:
        return None

    design = data.get("design", {})
    sections = data.get("sections", [])

    palette = design.get("palette", {})
    primary = palette.get("primary", "#6366f1")
    secondary = palette.get("secondary", "#1e1b4b")
    accent = palette.get("accent", "#f59e0b")
    bg = palette.get("bg", "#0f0f17")
    text_color = palette.get("text", "#e2e8f0")

    font_style = design.get("font", "modern")
    font_family, font_url = _FONTS.get(font_style, _FONTS["modern"])
    vibe = _e(design.get("vibe", ""))

    # Derive a lighter text color for secondary text
    # and a semi-transparent version of primary for backgrounds
    sections_html = []
    for section in sections:
        renderer = _SECTION_RENDERERS.get(section.get("type"))
        if renderer:
            sections_html.append(renderer(section, primary, accent))

    body_html = "\n".join(sections_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(business_name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{font_url}" rel="stylesheet">
<style>
:root {{
    --primary: {primary};
    --secondary: {secondary};
    --accent: {accent};
    --bg: {bg};
    --text: {text_color};
    --text-muted: color-mix(in srgb, {text_color} 60%, transparent);
    --surface: color-mix(in srgb, {text_color} 5%, {bg});
    --surface-hover: color-mix(in srgb, {text_color} 8%, {bg});
    --border: color-mix(in srgb, {text_color} 10%, transparent);
    --primary-glow: color-mix(in srgb, {primary} 15%, transparent);
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}

body {{
    font-family: {font_family};
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
}}

.container {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 1.5rem;
}}

/* ─── HERO ─── */
.hero {{
    padding: 8rem 0 6rem;
    text-align: center;
    position: relative;
    overflow: hidden;
}}
.hero::before {{
    content: '';
    position: absolute;
    top: -40%;
    left: 50%;
    transform: translateX(-50%);
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, var(--primary-glow) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
}}
.hero .container {{ position: relative; z-index: 1; }}
.hero h1 {{
    font-size: clamp(2.2rem, 5vw, 3.8rem);
    font-weight: 800;
    line-height: 1.15;
    margin-bottom: 1.2rem;
    letter-spacing: -0.02em;
}}
.hero .subheadline {{
    font-size: clamp(1rem, 2vw, 1.3rem);
    color: var(--text-muted);
    max-width: 600px;
    margin: 0 auto 2.5rem;
    line-height: 1.6;
}}
.hero .stats {{
    display: flex;
    justify-content: center;
    gap: 3rem;
    margin-top: 3rem;
    flex-wrap: wrap;
}}
.hero .stat {{ text-align: center; }}
.hero .stat-value {{
    font-size: 2rem;
    font-weight: 700;
    color: var(--primary);
}}
.hero .stat-label {{
    font-size: 0.85rem;
    color: var(--text-muted);
    margin-top: 0.25rem;
}}

/* ─── BUTTONS ─── */
.btn {{
    display: inline-block;
    padding: 0.85rem 2.2rem;
    border-radius: 8px;
    font-weight: 600;
    font-size: 1rem;
    text-decoration: none;
    transition: all 0.2s;
    cursor: pointer;
    border: none;
}}
.btn-primary {{
    background: var(--primary);
    color: {bg};
}}
.btn-primary:hover {{
    filter: brightness(1.15);
    transform: translateY(-1px);
    box-shadow: 0 4px 20px var(--primary-glow);
}}
.btn-secondary {{
    background: transparent;
    color: var(--text);
    border: 1px solid var(--border);
}}
.btn-secondary:hover {{
    background: var(--surface);
    border-color: var(--primary);
}}

/* ─── SECTIONS ─── */
section {{
    padding: 5rem 0;
}}
section h2 {{
    font-size: clamp(1.6rem, 3vw, 2.4rem);
    font-weight: 700;
    margin-bottom: 1rem;
    letter-spacing: -0.01em;
}}
.section-subtitle {{
    color: var(--text-muted);
    font-size: 1.1rem;
    margin-bottom: 3rem;
}}

/* ─── GRID CARDS ─── */
.card-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1.5rem;
}}
.card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.75rem;
    transition: all 0.2s;
}}
.card:hover {{
    background: var(--surface-hover);
    border-color: var(--primary);
    transform: translateY(-2px);
}}
.card-icon {{
    font-size: 1.8rem;
    margin-bottom: 0.75rem;
    display: block;
}}
.card h3 {{
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}}
.card p {{
    color: var(--text-muted);
    font-size: 0.95rem;
    line-height: 1.6;
}}

/* ─── TESTIMONIALS ─── */
.testimonials {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1.5rem;
}}
.testimonial {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.75rem;
}}
.testimonial blockquote {{
    font-size: 1rem;
    font-style: italic;
    line-height: 1.7;
    margin-bottom: 1rem;
    color: var(--text);
}}
.testimonial .author {{
    font-weight: 600;
    font-size: 0.9rem;
}}
.testimonial .role {{
    color: var(--text-muted);
    font-size: 0.85rem;
}}

/* ─── PRICING ─── */
.pricing-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1.5rem;
    align-items: start;
}}
.pricing-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 2rem;
    text-align: center;
    transition: all 0.2s;
}}
.pricing-card.highlighted {{
    border-color: var(--primary);
    position: relative;
    box-shadow: 0 0 30px var(--primary-glow);
}}
.pricing-card.highlighted::before {{
    content: 'Popular';
    position: absolute;
    top: -12px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--primary);
    color: {bg};
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.25rem 1rem;
    border-radius: 99px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
.pricing-name {{
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}}
.pricing-price {{
    font-size: 2.5rem;
    font-weight: 800;
    color: var(--primary);
    margin-bottom: 1.5rem;
}}
.pricing-features {{
    list-style: none;
    text-align: left;
    margin-bottom: 2rem;
}}
.pricing-features li {{
    padding: 0.4rem 0;
    color: var(--text-muted);
    font-size: 0.95rem;
}}
.pricing-features li::before {{
    content: '\2713';
    color: var(--primary);
    font-weight: 700;
    margin-right: 0.5rem;
}}

/* ─── FAQ ─── */
.faq-list {{ max-width: 700px; margin: 0 auto; }}
.faq-item {{
    border-bottom: 1px solid var(--border);
    padding: 1.5rem 0;
}}
.faq-item h3 {{
    font-size: 1.05rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
    color: var(--text);
}}
.faq-item p {{
    color: var(--text-muted);
    font-size: 0.95rem;
    line-height: 1.6;
}}

/* ─── CTA SECTION ─── */
.cta-section {{
    text-align: center;
    padding: 5rem 0 6rem;
}}
.cta-section h2 {{
    margin-bottom: 0.75rem;
}}
.cta-section .subheadline {{
    color: var(--text-muted);
    font-size: 1.1rem;
    margin-bottom: 2rem;
    max-width: 500px;
    margin-left: auto;
    margin-right: auto;
}}

/* ─── FOOTER ─── */
.site-footer {{
    text-align: center;
    padding: 2rem 0;
    border-top: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 0.8rem;
}}
.site-footer a {{
    color: var(--primary);
    text-decoration: none;
}}

/* ─── RESPONSIVE ─── */
@media (max-width: 768px) {{
    .hero {{ padding: 5rem 0 3rem; }}
    section {{ padding: 3rem 0; }}
    .hero .stats {{ gap: 1.5rem; }}
    .card-grid {{ grid-template-columns: 1fr; }}
    .pricing-grid {{ grid-template-columns: 1fr; max-width: 400px; margin: 0 auto; }}
}}
</style>
</head>
<body>

{body_html}

<footer class="site-footer">
    <div class="container">
        Powered by <a href="https://arclane.cloud">Arclane</a>
    </div>
</footer>

<!-- Signup Modal -->
<div id="signup-modal" style="display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.6);backdrop-filter:blur(4px);align-items:center;justify-content:center;">
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:2.5rem;max-width:420px;width:90%;position:relative;">
        <button onclick="document.getElementById('signup-modal').style.display='none'" style="position:absolute;top:1rem;right:1rem;background:none;border:none;color:var(--text-muted);font-size:1.5rem;cursor:pointer;line-height:1;">&times;</button>
        <h2 style="font-size:1.4rem;margin-bottom:0.5rem;">Get early access</h2>
        <p style="color:var(--text-muted);font-size:0.95rem;margin-bottom:1.5rem;">Be the first to know when {_e(business_name)} launches.</p>
        <form id="signup-form" onsubmit="return handleSignup(event)">
            <input id="signup-name" type="text" placeholder="Your name" style="width:100%;padding:0.75rem 1rem;margin-bottom:0.75rem;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:1rem;font-family:inherit;">
            <input id="signup-email" type="email" placeholder="you@example.com" required style="width:100%;padding:0.75rem 1rem;margin-bottom:1rem;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:1rem;font-family:inherit;">
            <button type="submit" class="btn btn-primary" style="width:100%;text-align:center;" id="signup-btn">Sign up</button>
        </form>
        <p id="signup-msg" style="display:none;text-align:center;margin-top:1rem;font-size:0.95rem;"></p>
    </div>
</div>

<script>
document.querySelectorAll('a[href="#signup"]').forEach(function(a) {{
    a.addEventListener('click', function(e) {{
        e.preventDefault();
        document.getElementById('signup-modal').style.display = 'flex';
        document.getElementById('signup-email').focus();
    }});
}});
document.getElementById('signup-modal').addEventListener('click', function(e) {{
    if (e.target === this) this.style.display = 'none';
}});
function handleSignup(e) {{
    e.preventDefault();
    var btn = document.getElementById('signup-btn');
    var msg = document.getElementById('signup-msg');
    btn.textContent = 'Signing up...';
    btn.disabled = true;
    fetch('/signup', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            name: document.getElementById('signup-name').value,
            email: document.getElementById('signup-email').value
        }})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
        if (data.ok) {{
            document.getElementById('signup-form').style.display = 'none';
            msg.style.display = 'block';
            msg.style.color = 'var(--primary)';
            msg.textContent = data.message || "You're in! We'll be in touch.";
        }} else {{
            msg.style.display = 'block';
            msg.style.color = 'var(--accent)';
            msg.textContent = data.error || 'Something went wrong.';
            btn.textContent = 'Sign up';
            btn.disabled = false;
        }}
    }})
    .catch(function() {{
        msg.style.display = 'block';
        msg.style.color = 'var(--accent)';
        msg.textContent = 'Connection error. Please try again.';
        btn.textContent = 'Sign up';
        btn.disabled = false;
    }});
    return false;
}}
""" + (_CHECKOUT_SCRIPT if has_stripe else "") + """
</script>

</body>
</html>"""


# ─── Section renderers ────────────────────────────────────────────────

@_renderer("hero")
def _render_hero(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    sub = _e(s.get("subheadline", ""))
    cta_text = _e(s.get("cta_text", "Get Started"))
    cta_url = _e(s.get("cta_url", "#signup"))

    stats_html = ""
    if s.get("stats"):
        items = "".join(
            f'<div class="stat"><div class="stat-value">{_e(st.get("value", ""))}</div>'
            f'<div class="stat-label">{_e(st.get("label", ""))}</div></div>'
            for st in s["stats"]
        )
        stats_html = f'<div class="stats">{items}</div>'

    return f"""
<section class="hero">
    <div class="container">
        <h1>{headline}</h1>
        <p class="subheadline">{sub}</p>
        <a href="{cta_url}" class="btn btn-primary">{cta_text}</a>
        {stats_html}
    </div>
</section>"""


@_renderer("problem")
def _render_problem(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    points = s.get("points", [])
    cards = "".join(
        f'<div class="card"><span class="card-icon">{_e(p.get("icon", ""))}</span>'
        f'<h3>{_e(p.get("title", ""))}</h3><p>{_e(p.get("description", ""))}</p></div>'
        for p in points
    )
    return f"""
<section>
    <div class="container">
        <h2>{headline}</h2>
        <div class="card-grid">{cards}</div>
    </div>
</section>"""


@_renderer("solution")
def _render_solution(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    subtitle = _e(s.get("subtitle", ""))
    features = s.get("features", [])
    cards = "".join(
        f'<div class="card"><span class="card-icon">{_e(f.get("icon", ""))}</span>'
        f'<h3>{_e(f.get("title", ""))}</h3><p>{_e(f.get("description", ""))}</p></div>'
        for f in features
    )
    sub_html = f'<p class="section-subtitle">{subtitle}</p>' if subtitle else ""
    return f"""
<section>
    <div class="container">
        <h2>{headline}</h2>
        {sub_html}
        <div class="card-grid">{cards}</div>
    </div>
</section>"""


@_renderer("proof")
def _render_proof(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    items = s.get("items", [])
    cards = "".join(
        f'<div class="testimonial"><blockquote>"{_e(t.get("quote", ""))}"</blockquote>'
        f'<div class="author">{_e(t.get("author", ""))}</div>'
        f'<div class="role">{_e(t.get("role", ""))}</div></div>'
        for t in items
    )
    return f"""
<section>
    <div class="container">
        <h2>{headline}</h2>
        <div class="testimonials">{cards}</div>
    </div>
</section>"""


@_renderer("pricing")
def _render_pricing(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    plans = s.get("plans", [])
    cards = []
    for p in plans:
        highlighted = "highlighted" if p.get("highlighted") else ""
        features_html = "".join(
            f"<li>{_e(f)}</li>" for f in p.get("features", [])
        )
        cta = _e(p.get("cta", "Get Started"))
        plan_name = _e(p.get("name", "Plan"))
        # Extract cents from price string (e.g. "$9.99" -> 999, "$49/month" -> 4900)
        price_str = p.get("price", "0")
        amount_attr = f'data-plan="{plan_name}" data-amount="{_e(price_str)}"'
        cards.append(
            f'<div class="pricing-card {highlighted}">'
            f'<div class="pricing-name">{plan_name}</div>'
            f'<div class="pricing-price">{_e(price_str)}</div>'
            f'<ul class="pricing-features">{features_html}</ul>'
            f'<a href="#signup" class="btn btn-primary pricing-cta" {amount_attr}>{cta}</a></div>'
        )
    return f"""
<section>
    <div class="container">
        <h2 style="text-align:center;margin-bottom:3rem;">{headline}</h2>
        <div class="pricing-grid">{"".join(cards)}</div>
    </div>
</section>"""


@_renderer("faq")
def _render_faq(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    items = s.get("items", [])
    faq_html = "".join(
        f'<div class="faq-item"><h3>{_e(q.get("question", ""))}</h3>'
        f'<p>{_e(q.get("answer", ""))}</p></div>'
        for q in items
    )
    return f"""
<section>
    <div class="container">
        <h2 style="text-align:center;">{headline}</h2>
        <div class="faq-list">{faq_html}</div>
    </div>
</section>"""


@_renderer("cta")
def _render_cta(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    sub = _e(s.get("subheadline", ""))
    cta_text = _e(s.get("cta_text", "Get Started"))
    cta_url = _e(s.get("cta_url", "#signup"))
    return f"""
<section class="cta-section">
    <div class="container">
        <h2>{headline}</h2>
        <p class="subheadline">{sub}</p>
        <a href="{cta_url}" class="btn btn-primary">{cta_text}</a>
    </div>
</section>"""


@_renderer("how_it_works")
def _render_how_it_works(s: dict, primary: str, accent: str) -> str:
    headline = _e(s.get("headline", ""))
    steps = s.get("steps", s.get("features", []))
    cards = []
    for i, step in enumerate(steps, 1):
        cards.append(
            f'<div class="card"><span class="card-icon" style="color:var(--primary);font-weight:700;">{i}</span>'
            f'<h3>{_e(step.get("title", ""))}</h3><p>{_e(step.get("description", ""))}</p></div>'
        )
    return f"""
<section>
    <div class="container">
        <h2 style="text-align:center;">{headline}</h2>
        <div class="card-grid">{"".join(cards)}</div>
    </div>
</section>"""
