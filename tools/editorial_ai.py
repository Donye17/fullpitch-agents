"""Small editorial Gemini helpers for titles and summaries."""

from __future__ import annotations

import logging
import re

from tools.gemini_relevance import GEMINI_FREE_TIER_MODEL
from tools.text_utils import clean_text

logger = logging.getLogger(__name__)


def _strip_trailing_punctuation(value: str) -> str:
    return re.sub(r"[\s.!?;:,-]+$", "", value.strip())


def _fallback_short_title(title: str) -> str:
    words = clean_text(title).split()
    return _strip_trailing_punctuation(" ".join(words[:7]))


def shorten_title(title: str, client=None) -> str:
    """Rewrite long article titles to a clean 5-7 word headline."""
    cleaned = _strip_trailing_punctuation(clean_text(title))
    if len(cleaned.split()) <= 7:
        return cleaned
    if client is None:
        return _fallback_short_title(cleaned)

    try:
        response = client.models.generate_content(
            model=GEMINI_FREE_TIER_MODEL,
            contents=(
                "Rewrite this headline to 5-7 words maximum.\n"
                "Keep the most important facts.\n"
                "No punctuation at end. No clickbait.\n"
                "Headline style, capitalize key words.\n\n"
                f"Original: {cleaned}\n\n"
                "Reply with ONLY the shortened headline, nothing else."
            ),
        )
        shortened = _strip_trailing_punctuation(clean_text(response.text))
        if not shortened:
            return _fallback_short_title(cleaned)
        return _fallback_short_title(shortened) if len(shortened.split()) > 7 else shortened
    except Exception:
        logger.exception("Gemini title shortening failed for %s", cleaned[:80])
        return _fallback_short_title(cleaned)


def normalize_feed_summary(value: str | None, max_words: int = 60) -> str:
    """Keep feed summaries one paragraph and bounded."""
    summary = clean_text((value or "").replace("\n\n", " "))
    words = summary.split()
    if len(words) <= max_words:
        return summary
    return _strip_trailing_punctuation(" ".join(words[:max_words]))
