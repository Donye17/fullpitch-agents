"""Text cleaning utilities for AI-generated content.

Strips characters that signal AI writing (em dashes, en dashes, etc.)
before content is saved to the database.
"""

from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """Remove or replace characters that signal AI writing."""
    text = text.replace("\u2014", ",")   # em dash → comma
    text = text.replace("\u2013", "-")   # en dash → hyphen
    text = re.sub(r" {2,}", " ", text)
    return text.strip()
