"""Helpers for reading the server's configured local date and time."""

from __future__ import annotations

from datetime import date, datetime


def local_now() -> datetime:
    """Return the current server-local time with its UTC offset."""
    return datetime.now().astimezone()


def local_today() -> date:
    """Return the current date in the server's local timezone."""
    return local_now().date()
