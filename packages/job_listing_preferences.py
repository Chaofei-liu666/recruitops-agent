"""Local job-browser listing preferences.

The desktop workbench is served from a per-launch loopback origin
(``127.0.0.1:<random high port>``), so browser storage cannot survive a restart.
The companies a user chose to hide are therefore kept next to the other local
settings in ``.data/settings``.

Only the exclusion list lives here: it is a view preference, never business
data, and it must never change what is stored for a job.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import json

from packages.automation.latest_report import write_json_atomic
from packages.user_settings import settings_dir

FILE_NAME = "job_listing_filters.json"
SCHEMA_VERSION = 1
MAX_HIDDEN_COMPANIES = 500
MAX_COMPANY_KEY_LENGTH = 255


class JobListingPreferenceError(ValueError):
    """Raised when a submitted hidden-company list is not acceptable."""


def _normalize(values: object) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise JobListingPreferenceError("hidden_companies must be a list")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise JobListingPreferenceError("hidden company keys must be strings")
        key = value.strip().casefold()
        if not key:
            continue
        if len(key) > MAX_COMPANY_KEY_LENGTH:
            raise JobListingPreferenceError("hidden company key is too long")
        if key not in normalized:
            normalized.append(key)
    if len(normalized) > MAX_HIDDEN_COMPANIES:
        raise JobListingPreferenceError("too many hidden companies")
    return normalized


def preferences_path(settings) -> Path:
    return settings_dir(settings) / FILE_NAME


def read_hidden_companies(settings) -> list[str]:
    """Return the saved exclusion keys, or an empty list when absent/unreadable."""

    path = preferences_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return []
    try:
        return _normalize(payload.get("hidden_companies") or [])
    except JobListingPreferenceError:
        return []


def write_hidden_companies(settings, values: object) -> list[str]:
    """Persist the exclusion keys atomically and return the normalized list."""

    normalized = _normalize(values)
    write_json_atomic(
        preferences_path(settings),
        {"schema_version": SCHEMA_VERSION, "hidden_companies": normalized},
    )
    return normalized
