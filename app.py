"""Steel Challenge production Streamlit entry point."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import streamlit as st

from auth_utils import AuthContext, ConfigurationError, create_service_client, restore_session
from config import public_access_enabled
from ui_components import (
    apply_theme,
    render_header,
    render_login_page,
    render_navigation,
    render_page,
    render_pending_page,
)


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("steel_challenge_app")

st.set_page_config(
    page_title="Steel Challenge Dashboard",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()

public_site_enabled = public_access_enabled()
admin_mode_requested = str(st.query_params.get("admin", "")).casefold() in {
    "1",
    "true",
    "yes",
}

if public_site_enabled and not admin_mode_requested:
    try:
        auth_context = AuthContext(
            client=create_service_client(),
            user=SimpleNamespace(id="public", email=""),
            profile={
                "approved": True,
                "can_view": True,
                "can_download": True,
                "is_admin": False,
                "public_access": True,
                "display_name": "Public Dashboard",
            },
        )
    except ConfigurationError as exc:
        st.error(str(exc))
        st.info("공개 모드에는 서버용 Supabase Secret Key가 필요합니다.")
        st.stop()
else:
    try:
        auth_context = restore_session()
    except ConfigurationError as exc:
        st.error(str(exc))
        st.info(".streamlit/secrets.toml 또는 환경변수에 Supabase 설정을 추가하세요.")
        st.stop()
    except Exception:
        LOGGER.exception("Could not restore auth session")
        st.error("로그인 세션을 확인하는 중 문제가 발생했습니다. 다시 로그인해 주세요.")
        auth_context = None

if auth_context is None:
    render_login_page()
    st.stop()

if not auth_context.profile.get("public_access") and not (
    auth_context.profile.get("approved")
    and auth_context.profile.get("can_view")
):
    render_pending_page(auth_context)
    st.stop()

selected_page = render_navigation(auth_context.profile)
render_header(auth_context)

try:
    render_page(selected_page, auth_context)
except PermissionError as exc:
    LOGGER.warning("Permission denied on %s: %s", selected_page, exc)
    st.error(str(exc))
except Exception:
    LOGGER.exception("Page rendering failed: %s", selected_page)
    st.error("데이터를 불러오는 중 문제가 발생했습니다. 잠시 후 다시 시도하세요.")
