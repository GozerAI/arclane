"""Content repurposer — transforms content between formats to maximize distribution.

Multiplies the value of every content piece by enabling:
- Blog → Twitter thread (5-7 tweets)
- Blog → LinkedIn carousel (slide outline)
- Report → Executive summary (3 bullet points)
- Social → Blog expansion (full article from hook)
- Any → Markdown export
- Email sequence → Individual email variants
"""

from arclane.core.logging import get_logger

log = get_logger("content_repurposer")


def repurpose(content_type: str, title: str, body: str, target_format: str) -> dict:
    """Transform content into a different format.

    Args:
        content_type: Source type (blog, social, newsletter, report, changelog)
        title: Source title
        body: Source body
        target_format: Target format (twitter_thread, linkedin_carousel, executive_summary,
                       blog_expansion, markdown, email_variant, key_takeaways, quote_cards)

    Returns:
        Dict with 'format', 'title', 'body', 'pieces' (for multi-part formats).
    """
    formatter = _FORMATTERS.get(target_format)
    if not formatter:
        return {"error": f"Unknown format: {target_format}", "available": list(_FORMATTERS.keys())}

    return formatter(content_type, title, body)


def available_formats(content_type: str) -> list[dict]:
    """Return which target formats are available for a given content type."""
    formats = []
    for fmt, info in _FORMAT_INFO.items():
        if content_type in info["from_types"] or "*" in info["from_types"]:
            formats.append({
                "format": fmt,
                "label": info["label"],
                "description": info["description"],
            })
    return formats


# --- Format handlers ---

def _to_twitter_thread(content_type: str, title: str, body: str) -> dict:
    """Convert content into a Twitter thread (5-7 tweets)."""
    sentences = [s.strip() for s in body.replace("\n", " ").split(".") if s.strip() and len(s.strip()) > 20]

    tweets = []
    # Tweet 1: Hook
    hook = sentences[0] if sentences else title
    tweets.append(f"{hook}.\n\nA thread 🧵")

    # Middle tweets: Key points (group sentences into ~240 char chunks)
    chunk = ""
    for s in sentences[1:]:
        candidate = f"{chunk} {s}.".strip() if chunk else f"{s}."
        if len(candidate) > 240:
            if chunk:
                tweets.append(chunk)
            chunk = f"{s}."
        else:
            chunk = candidate
    if chunk:
        tweets.append(chunk)

    # Cap at 7 tweets
    tweets = tweets[:7]

    # Final tweet: CTA
    if len(tweets) < 7:
        tweets.append(f"If this was useful, follow for more on {title.split()[0] if title else 'this topic'}.\n\nRetweet the first tweet to share.")

    return {
        "format": "twitter_thread",
        "title": f"Thread: {title}",
        "body": "\n\n---\n\n".join(f"[{i+1}/{len(tweets)}] {t}" for i, t in enumerate(tweets)),
        "pieces": tweets,
        "piece_count": len(tweets),
    }


def _to_linkedin_carousel(content_type: str, title: str, body: str) -> dict:
    """Convert content into a LinkedIn carousel outline (slide-by-slide)."""
    paragraphs = [p.strip() for p in body.split("\n") if p.strip() and len(p.strip()) > 15]

    slides = []
    # Slide 1: Title slide
    slides.append({"slide": 1, "heading": title, "body": "Swipe to learn more →"})

    # Content slides: one per key paragraph/section
    for i, para in enumerate(paragraphs[:8]):
        # Extract first sentence as heading
        first_sentence = para.split(".")[0].strip()
        remaining = para[len(first_sentence):].strip().lstrip(".")
        slides.append({
            "slide": i + 2,
            "heading": first_sentence[:80],
            "body": remaining[:200] if remaining else para[:200],
        })

    # Final slide: CTA
    slides.append({
        "slide": len(slides) + 1,
        "heading": "Want more?",
        "body": f"Follow for insights on {title.split()[0] if title else 'business growth'}.\nLike ❤️ and repost ♻️ if this was helpful.",
    })

    formatted = "\n\n".join(
        f"**Slide {s['slide']}**\n# {s['heading']}\n{s['body']}"
        for s in slides
    )

    return {
        "format": "linkedin_carousel",
        "title": f"Carousel: {title}",
        "body": formatted,
        "pieces": slides,
        "piece_count": len(slides),
    }


def _to_executive_summary(content_type: str, title: str, body: str) -> dict:
    """Distill content into a 3-bullet executive summary."""
    paragraphs = [p.strip() for p in body.split("\n") if p.strip() and len(p.strip()) > 30]

    bullets = []
    for para in paragraphs[:3]:
        # Take the first meaningful sentence
        sentence = para.split(".")[0].strip()
        if len(sentence) > 20:
            bullets.append(sentence + ".")

    while len(bullets) < 3:
        bullets.append(f"Review the full {content_type} for additional detail.")

    summary = f"# Executive Summary: {title}\n\n" + "\n".join(f"- {b}" for b in bullets[:3])

    return {
        "format": "executive_summary",
        "title": f"Summary: {title}",
        "body": summary,
        "pieces": bullets[:3],
        "piece_count": 3,
    }


def _to_blog_expansion(content_type: str, title: str, body: str) -> dict:
    """Expand a short piece (social post, summary) into a blog article outline."""
    return {
        "format": "blog_expansion",
        "title": f"Blog: {title}",
        "body": (
            f"# {title}\n\n"
            f"## Introduction\n{body[:300]}\n\n"
            f"## The Problem\nExpand on the pain point described above. Add data, examples, or anecdotes.\n\n"
            f"## The Solution\nExplain how to solve it. Be specific and actionable.\n\n"
            f"## Key Takeaways\n- Takeaway 1\n- Takeaway 2\n- Takeaway 3\n\n"
            f"## Next Steps\nTell the reader what to do next. Include a CTA.\n"
        ),
        "pieces": ["introduction", "problem", "solution", "takeaways", "cta"],
        "piece_count": 5,
    }


def _to_markdown(content_type: str, title: str, body: str) -> dict:
    """Export content as clean Markdown."""
    md = f"# {title}\n\n{body}"
    return {
        "format": "markdown",
        "title": title,
        "body": md,
        "pieces": [md],
        "piece_count": 1,
    }


def _to_email_variant(content_type: str, title: str, body: str) -> dict:
    """Convert content into an email-ready format with subject line options."""
    # Generate 3 subject line variants
    words = title.split()
    subjects = [
        title,
        f"Quick read: {title}" if len(title) < 50 else title[:50],
        f"You need to know this about {words[-1] if words else 'this'}" if words else title,
    ]

    # Format body for email
    email_body = f"Hi there,\n\n{body[:500]}\n\nBest,\n[Your name]"

    return {
        "format": "email_variant",
        "title": title,
        "body": email_body,
        "pieces": subjects,
        "piece_count": len(subjects),
        "subject_lines": subjects,
    }


def _to_key_takeaways(content_type: str, title: str, body: str) -> dict:
    """Extract key takeaways as a bulleted list."""
    sentences = [s.strip() + "." for s in body.replace("\n", " ").split(".") if len(s.strip()) > 25]
    takeaways = sentences[:7]

    formatted = f"# Key Takeaways: {title}\n\n" + "\n".join(f"- {t}" for t in takeaways)

    return {
        "format": "key_takeaways",
        "title": f"Takeaways: {title}",
        "body": formatted,
        "pieces": takeaways,
        "piece_count": len(takeaways),
    }


def _to_quote_cards(content_type: str, title: str, body: str) -> dict:
    """Extract quotable statements for social media images/cards."""
    sentences = [s.strip() for s in body.replace("\n", " ").split(".") if 30 < len(s.strip()) < 150]
    quotes = [f'"{s}."' for s in sentences[:5]]

    return {
        "format": "quote_cards",
        "title": f"Quotes from: {title}",
        "body": "\n\n".join(quotes),
        "pieces": quotes,
        "piece_count": len(quotes),
    }


_FORMATTERS = {
    "twitter_thread": _to_twitter_thread,
    "linkedin_carousel": _to_linkedin_carousel,
    "executive_summary": _to_executive_summary,
    "blog_expansion": _to_blog_expansion,
    "markdown": _to_markdown,
    "email_variant": _to_email_variant,
    "key_takeaways": _to_key_takeaways,
    "quote_cards": _to_quote_cards,
}

_FORMAT_INFO = {
    "twitter_thread": {"label": "Twitter Thread", "description": "5-7 tweet thread with hook and CTA", "from_types": {"blog", "report", "newsletter"}},
    "linkedin_carousel": {"label": "LinkedIn Carousel", "description": "Slide-by-slide carousel outline", "from_types": {"blog", "report"}},
    "executive_summary": {"label": "Executive Summary", "description": "3-bullet distillation", "from_types": {"report", "blog", "newsletter"}},
    "blog_expansion": {"label": "Blog Article", "description": "Expand into a full blog post", "from_types": {"social", "changelog"}},
    "markdown": {"label": "Markdown Export", "description": "Clean Markdown for docs/GitHub", "from_types": {"*"}},
    "email_variant": {"label": "Email Format", "description": "Email-ready with 3 subject line variants", "from_types": {"blog", "newsletter", "report"}},
    "key_takeaways": {"label": "Key Takeaways", "description": "Bulleted takeaway list", "from_types": {"blog", "report", "newsletter"}},
    "quote_cards": {"label": "Quote Cards", "description": "Quotable statements for social graphics", "from_types": {"blog", "report", "newsletter"}},
}
