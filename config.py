"""Runtime configuration helpers.

Environment variables are preferred so the same modules work in GitHub Actions.
When the app runs in Streamlit, ``st.secrets`` is used as a fallback.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing."""


def _streamlit_secret(name: str) -> Optional[str]:
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_secret(
    name: str,
    *,
    required: bool = True,
    aliases: Iterable[str] = (),
) -> Optional[str]:
    """Return a secret without ever logging its value."""

    names = (name, *tuple(aliases))
    for candidate in names:
        value = os.getenv(candidate)
        if value and value.strip():
            return value.strip()

    for candidate in names:
        value = _streamlit_secret(candidate)
        if value:
            return value

    if required:
        joined = ", ".join(names)
        raise ConfigurationError(f"필수 설정이 없습니다: {joined}")
    return None


def get_supabase_url() -> str:
    return str(get_secret("SUPABASE_URL"))


def get_supabase_anon_key() -> str:
    # SUPABASE_KEY is retained as a compatibility alias for the existing app.
    return str(get_secret("SUPABASE_ANON_KEY", aliases=("SUPABASE_KEY",)))


def get_supabase_service_key(*, required: bool = False) -> Optional[str]:
    return get_secret(
        "SUPABASE_SERVICE_ROLE_KEY",
        required=required,
        aliases=("SUPABASE_SECRET_KEY",),
    )


def public_access_enabled() -> bool:
    """Return whether the read-only public dashboard mode is enabled."""

    value = get_secret("PUBLIC_ACCESS_ENABLED", required=False)
    return str(value or "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
