"""Shared scheduling helpers for expensive agent tasks."""

from __future__ import annotations

from datetime import datetime


def is_tournament_active() -> bool:
    """Return True on weekend days, when most HS/WER matches happen."""
    return datetime.now().weekday() >= 5
