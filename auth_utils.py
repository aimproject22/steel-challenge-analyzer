"""Supabase Auth helpers with one client per Streamlit user session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from supabase import create_client

from config import (
    ConfigurationError,
    get_supabase_anon_key,
    get_supabase_service_key,
    get_supabase_url,
)

try:
    # supabase-py 2.31+ keeps a legacy ClientOptions class beside the
    # synchronous options class. create_client() requires SyncClientOptions;
    # the legacy class lacks the `storage` attribute and crashes at startup.
    from supabase.lib.client_options import SyncClientOptions as ClientOptions
except ImportError:  # pragma: no cover - compatibility with older supabase-py
    try:
        from supabase.lib.client_options import ClientOptions
    except ImportError:
        ClientOptions = None  # type: ignore


SESSION_KEYS = (
    "access_token",
    "refresh_token",
    "auth_user_id",
    "auth_email",
    "profile",
)


@dataclass
class AuthContext:
    client: Any
    user: Any
    profile: dict


class LoginStageError(RuntimeError):
    """A safe login failure tagged with the stage where it occurred."""

    def __init__(self, stage: str, detail: str):
        self.stage = stage
        self.detail = detail
        super().__init__(detail)


def _new_client(key: str, *, server: bool = False):
    if ClientOptions is None:
        return create_client(get_supabase_url(), key)
    options = ClientOptions(
        auto_refresh_token=not server,
        persist_session=False,
    )
    return create_client(get_supabase_url(), key, options=options)


def create_anon_client():
    return _new_client(get_supabase_anon_key())


def create_service_client():
    key = get_supabase_service_key(required=True)
    return _new_client(str(key), server=True)


def _profile_for_user(client: Any, user_id: str) -> dict:
    response = (
        client.table("profiles")
        .select(
            "id,auth_user_id,email,display_name,approved,can_view,"
            "can_download,is_admin,created_at,updated_at"
        )
        .eq("auth_user_id", user_id)
        .limit(1)
        .execute()
    )
    return dict(response.data[0]) if response.data else {}


def _save_session(session: Any, user: Any, profile: dict) -> None:
    import streamlit as st

    st.session_state["access_token"] = session.access_token
    st.session_state["refresh_token"] = session.refresh_token
    st.session_state["auth_user_id"] = str(user.id)
    st.session_state["auth_email"] = str(user.email or "")
    st.session_state["profile"] = profile


def sign_in(email: str, password: str) -> AuthContext:
    client = create_anon_client()
    try:
        response = client.auth.sign_in_with_password(
            {"email": email.strip(), "password": password}
        )
    except Exception as exc:
        raise LoginStageError("credentials", str(exc)) from exc
    if response.session is None or response.user is None:
        raise LoginStageError("credentials", "로그인 세션을 만들지 못했습니다.")
    # get_user validates the access token with Supabase instead of trusting local state.
    try:
        verified = client.auth.get_user(response.session.access_token)
    except Exception as exc:
        raise LoginStageError("session", str(exc)) from exc
    user = verified.user or response.user
    try:
        profile = _profile_for_user(client, str(user.id))
    except Exception as exc:
        raise LoginStageError("profile", str(exc)) from exc
    _save_session(response.session, user, profile)
    return AuthContext(client=client, user=user, profile=profile)


def restore_session() -> Optional[AuthContext]:
    import streamlit as st

    access_token = st.session_state.get("access_token")
    refresh_token = st.session_state.get("refresh_token")
    if not access_token or not refresh_token:
        return None

    client = create_anon_client()
    try:
        response = client.auth.set_session(access_token, refresh_token)
        if response.session is None:
            clear_session_state()
            return None
        verified = client.auth.get_user(response.session.access_token)
        user = verified.user
    except Exception:
        # Expired, revoked, or malformed tokens must never leave stale auth
        # state attached to the Streamlit browser session.
        clear_session_state()
        return None
    if user is None:
        clear_session_state()
        return None
    profile = _profile_for_user(client, str(user.id))
    _save_session(response.session, user, profile)
    return AuthContext(client=client, user=user, profile=profile)


def clear_session_state() -> None:
    import streamlit as st

    for key in SESSION_KEYS:
        st.session_state.pop(key, None)


def sign_out(client: Any) -> None:
    try:
        client.auth.sign_out()
    finally:
        clear_session_state()


def require_fresh_admin(client: Any, user_id: str) -> dict:
    profile = _profile_for_user(client, user_id)
    if not (
        profile.get("approved")
        and profile.get("can_view")
        and profile.get("is_admin")
    ):
        raise PermissionError("관리자 권한이 필요합니다.")
    return profile


def invite_user(
    current_client: Any,
    current_user_id: str,
    email: str,
    *,
    display_name: str = "",
    redirect_to: str = "",
) -> Any:
    """Invite a user only after rechecking the caller's admin profile."""

    require_fresh_admin(current_client, current_user_id)
    service_client = create_service_client()
    options: dict[str, Any] = {}
    if display_name.strip():
        options["data"] = {"display_name": display_name.strip()}
    if redirect_to.strip():
        options["redirect_to"] = redirect_to.strip()
    if options:
        return service_client.auth.admin.invite_user_by_email(email.strip(), options)
    return service_client.auth.admin.invite_user_by_email(email.strip())


__all__ = [
    "AuthContext",
    "ConfigurationError",
    "LoginStageError",
    "clear_session_state",
    "create_anon_client",
    "create_service_client",
    "invite_user",
    "require_fresh_admin",
    "restore_session",
    "sign_in",
    "sign_out",
]
