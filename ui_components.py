"""Authenticated Korean Streamlit UI for the Steel Challenge platform."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from active_learning import generate_candidates
from auth_utils import AuthContext, LoginStageError, invite_user, sign_in, sign_out
from config import ConfigurationError, get_supabase_service_key
from db_utils import (
    assert_admin,
    assert_can_download,
    load_all_logs_df,
    load_all_runs_df,
    load_dashboard_metrics,
    load_data_quality_metrics,
    load_email_messages_df,
    load_logs_df,
    load_profiles_df,
    load_run_record,
    load_runs_df,
    save_to_db,
    update_profile_permissions,
)
from excel_utils import (
    build_all_runs_export_df,
    build_excel_frames,
    make_excel_from_frames,
    summarize_export_frame,
    summarize_export_frames,
)
from ml_engine import (
    estimate_model_uncertainty,
    generate_recommendations,
    get_feature_columns,
    train_model,
)
from parser import parse_docx_canonical
from visualization import (
    feature_scatter,
    grouped_average,
    grouped_count,
    histogram,
    line_by_run_date,
    process_timeline,
    scatter,
)


LOGGER = logging.getLogger("steel_challenge_ui")
SEOUL = ZoneInfo("Asia/Seoul")
RUN_COLUMNS = [
    "Run Date",
    "Process Type",
    "Sender",
    "Sender Email",
    "Steel User ID",
    "Steel Grade",
    "Status",
    "Score",
    "Cost Per Tonne",
    "Time",
    "Tap Temperature",
]


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root { --steel-orange: #f97316; }
        .stApp { background: linear-gradient(180deg, #f8fafc 0%, #eef2f7 100%); }
        [data-testid="stSidebar"] { background: #111827; }
        [data-testid="stSidebar"] * { color: #f8fafc; }
        [data-testid="stMetric"] {
            background: white; border: 1px solid #dbe3ec;
            border-left: 4px solid var(--steel-orange); padding: .8rem 1rem;
            border-radius: .65rem; box-shadow: 0 2px 8px #0f172a10;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_login_page() -> None:
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.markdown("## Steel Challenge 로그인")
        st.caption("관리자가 생성하고 승인한 계정만 사용할 수 있습니다.")
        with st.form("login_form"):
            email = st.text_input("ID / Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "로그인", use_container_width=True, type="primary"
            )
        if not submitted:
            return
        if not email.strip() or not password:
            st.warning("이메일과 비밀번호를 입력하세요.")
            return
        try:
            context = sign_in(email, password)
        except ConfigurationError as exc:
            st.error(str(exc))
            return
        except LoginStageError as exc:
            LOGGER.exception("Login failed at %s stage", exc.stage)
            error_text = exc.detail.casefold()
            if exc.stage == "credentials":
                if "email not confirmed" in error_text:
                    st.error("인증 실패: 이메일 확인이 완료되지 않은 계정입니다.")
                elif "invalid login credentials" in error_text:
                    st.error("인증 실패: 이메일 또는 비밀번호가 일치하지 않습니다.")
                elif "invalid api key" in error_text:
                    st.error("설정 실패: Supabase 공개 API key가 올바르지 않습니다.")
                else:
                    st.error(f"인증 요청 실패: {exc.detail}")
            elif exc.stage == "session":
                st.error(f"인증은 되었지만 세션 검증에 실패했습니다: {exc.detail}")
            else:
                st.error(f"인증은 되었지만 프로필 조회에 실패했습니다: {exc.detail}")
            return
        except Exception as exc:
            LOGGER.exception("Login failed")
            error_text = str(exc).casefold()
            if "email not confirmed" in error_text:
                st.error("이메일 확인이 완료되지 않은 계정입니다. Supabase Auth에서 확인 처리하세요.")
            elif "invalid login credentials" in error_text:
                st.error("이메일 또는 비밀번호가 일치하지 않습니다.")
            elif "invalid api key" in error_text:
                st.error("Supabase 공개 API key가 올바르지 않습니다.")
            else:
                st.error("로그인에 실패했습니다. 계정 정보 또는 서버 설정을 확인하세요.")
            return
        if not (context.profile.get("approved") and context.profile.get("can_view")):
            st.warning("관리자 승인이 필요한 계정입니다.")
        st.rerun()


def render_pending_page(context: AuthContext) -> None:
    st.title("Steel Challenge Dashboard")
    st.warning("관리자 승인이 필요한 계정입니다.")
    st.caption(str(context.user.email or ""))
    if st.button("로그아웃"):
        sign_out(context.client)
        st.rerun()


def render_header(context: AuthContext) -> None:
    is_public = bool(context.profile.get("public_access"))
    title_col, user_col, refresh_col, logout_col = st.columns([5, 2, 1, 1])
    with title_col:
        st.title("Steel Challenge Dashboard")
        st.caption("Electric Arc Furnace · Secondary Steelmaking 결과 분석 플랫폼")
    with user_col:
        if is_public:
            st.markdown("**Public Dashboard**")
            st.caption("로그인 없이 조회 중")
        else:
            st.markdown(f"**{context.profile.get('display_name') or context.user.email}**")
            st.caption(str(context.user.email or ""))
    with refresh_col:
        if st.button("새로고침", use_container_width=True):
            st.rerun()
    with logout_col:
        if is_public:
            if st.button("관리자 로그인", use_container_width=True):
                st.query_params["admin"] = "1"
                st.rerun()
        elif st.button("로그아웃", use_container_width=True):
            sign_out(context.client)
            st.query_params.clear()
            st.rerun()


def page_names(profile: Mapping[str, Any]) -> list[str]:
    pages = [
        "Dashboard",
        "전체 Run",
        "Run 상세",
        "시각화",
        "ML 분석",
        "Active Learning",
    ]
    if profile.get("public_access"):
        pages.append("다운로드")
        return pages
    pages.append("수동 업로드")
    if profile.get("can_download"):
        pages.append("다운로드")
    if profile.get("is_admin"):
        pages.extend(["데이터 품질", "사용자 관리"])
    return pages


def render_navigation(profile: Mapping[str, Any]) -> str:
    st.sidebar.markdown("### STEEL CONTROL")
    st.sidebar.caption("Production analytics")
    return st.sidebar.radio(
        "메뉴", page_names(profile), label_visibility="collapsed"
    )


def _filters(prefix: str) -> dict[str, Any]:
    with st.expander("필터", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            process_type = st.selectbox(
                "Process Type",
                ["전체", "Electric Arc Furnace", "Secondary Steelmaking"],
                key=f"{prefix}_process_type",
            )
            grade = st.text_input("Steel Grade (정확히 일치)", key=f"{prefix}_grade")
            steel_user = st.text_input(
                "Steel Challenge User ID (정확히 일치)",
                key=f"{prefix}_steel_user",
            )
        with col2:
            sender = st.text_input(
                "Sender Email (정확히 일치)", key=f"{prefix}_sender"
            )
            status = st.text_input(
                "Status (숫자, 비우면 전체)", key=f"{prefix}_status"
            )
        with col3:
            use_dates = st.checkbox("날짜 범위 사용", key=f"{prefix}_use_dates")
            today = date.today()
            selected_dates = st.date_input(
                "Run Date 범위",
                value=(today.replace(day=1), today),
                key=f"{prefix}_dates",
                disabled=not use_dates,
            )

    result: dict[str, Any] = {}
    if process_type != "전체":
        result["process_type"] = process_type
    if grade.strip():
        result["steel_grade"] = grade.strip()
    if steel_user.strip():
        result["steel_user_id"] = steel_user.strip()
    if sender.strip():
        result["sender_email"] = sender.strip()
    if status.strip():
        try:
            result["status"] = int(status.strip())
        except ValueError:
            st.warning("Status 필터는 정수여야 하므로 이번 조회에서는 제외했습니다.")
    if use_dates:
        values = (
            list(selected_dates)
            if isinstance(selected_dates, (tuple, list))
            else [selected_dates]
        )
        result["date_from"] = datetime.combine(values[0], time.min, SEOUL).isoformat()
        result["date_to"] = datetime.combine(values[-1], time.max, SEOUL).isoformat()
    return result


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _metric_text(value: Any, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _fallback_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    cost = _numeric(frame, "Cost Per Tonne")
    process_time = _numeric(frame, "Time")
    users = frame.get(
        "Steel User ID", pd.Series(dtype="object")
    ).dropna().nunique()
    return {
        "total_runs": len(frame),
        "participant_count": users,
        "average_cost": cost.mean(),
        "best_cost": cost.min(),
        "average_process_time": process_time.mean(),
    }


def _load_metrics(
    client: Any, filters: Mapping[str, Any], fallback: pd.DataFrame
) -> dict[str, Any]:
    try:
        return load_dashboard_metrics(client, filters=filters) or _fallback_metrics(
            fallback
        )
    except Exception:
        LOGGER.exception("dashboard_metrics RPC failed")
        return _fallback_metrics(fallback)


def _search_visible(frame: pd.DataFrame, keyword: str) -> pd.DataFrame:
    if frame.empty or not keyword.strip():
        return frame
    needle = keyword.strip().casefold()
    mask = frame.astype(str).apply(
        lambda column: column.str.casefold().str.contains(
            needle, regex=False, na=False
        )
    ).any(axis=1)
    return frame.loc[mask]


def _run_table(frame: pd.DataFrame) -> None:
    columns = [column for column in RUN_COLUMNS if column in frame.columns]
    if "Run ID" in frame.columns:
        columns.insert(0, "Run ID")
    st.dataframe(frame[columns], use_container_width=True, hide_index=True)


def render_dashboard(context: AuthContext) -> None:
    st.subheader("Dashboard")
    filters = _filters("dashboard")
    frame = load_runs_df(context.client, limit=250, filters=filters)
    metrics = _load_metrics(context.client, filters, frame)
    values = [
        ("Total Runs", f"{int(metrics.get('total_runs') or 0):,}"),
        ("참여 사용자 수", f"{int(metrics.get('participant_count') or 0):,}"),
        ("Average Cost / tonne", _metric_text(metrics.get("average_cost"))),
        ("Best Cost / tonne", _metric_text(metrics.get("best_cost"))),
        ("Average Process Time", _metric_text(metrics.get("average_process_time"))),
    ]
    for column, (label, value) in zip(st.columns(5), values):
        column.metric(label, value)
    if frame.empty:
        st.info("현재 필터에 해당하는 Run이 없습니다.")
        return
    st.markdown("#### 최신 Run")
    _run_table(frame.head(20))
    left, right = st.columns(2)
    left.plotly_chart(
        line_by_run_date(frame, "Cost Per Tonne", "Run Date vs Cost Per Tonne"),
        use_container_width=True,
    )
    right.plotly_chart(
        line_by_run_date(frame, "Time", "Run Date vs Process Time"),
        use_container_width=True,
    )


def render_all_runs(context: AuthContext) -> None:
    st.subheader("전체 Run")
    filters = _filters("runs")
    search_col, size_col, page_col = st.columns([2, 1, 1])
    keyword = search_col.text_input("현재 페이지 검색", key="runs_search")
    page_size = int(size_col.selectbox("페이지 크기", [25, 50, 100], index=1))
    page = int(page_col.number_input("페이지", min_value=1, value=1, step=1))
    frame = load_runs_df(
        context.client,
        limit=page_size,
        offset=(page - 1) * page_size,
        filters=filters,
    )
    total = int(_load_metrics(context.client, filters, frame).get("total_runs") or 0)
    st.caption(
        f"총 {total:,}건 · {page}/{max(1, math.ceil(total / page_size))} 페이지"
    )
    visible = _search_visible(frame, keyword)
    if visible.empty:
        st.info("표시할 Run이 없습니다.")
        return
    _run_table(visible)


def _section_frame(data: Mapping[str, Any], section: str) -> pd.DataFrame:
    prefix = f"{section} > "
    if section in {"Steel Composition", "Slag Composition"}:
        elements: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            if not str(key).startswith(prefix):
                continue
            parts = str(key)[len(prefix) :].split(" > ")
            element = parts[0]
            if len(parts) == 1:
                elements.setdefault(element, {})["Current"] = value
            elif parts[-1] in {"Current", "Min", "Max"}:
                elements.setdefault(element, {})[parts[-1]] = value
        return pd.DataFrame(
            [{"Element": element, **values} for element, values in elements.items()]
        )
    rows = []
    for key, value in data.items():
        if not str(key).startswith(prefix):
            continue
        item = str(key)[len(prefix) :]
        if " > " not in item and not isinstance(value, (dict, list)):
            rows.append({"Item": item, "Value": value})
    return pd.DataFrame(rows)


def render_run_detail(context: AuthContext) -> None:
    st.subheader("Run 상세")
    recent = load_runs_df(context.client, limit=200)
    if recent.empty:
        st.info("저장된 Run이 없습니다.")
        return
    ids = [int(value) for value in recent["Run ID"].dropna().tolist()]
    labels = {
        int(row["Run ID"]): (
            f"#{int(row['Run ID'])} · {row.get('Run Date', '—')} · "
            f"{row.get('Process Type', '—')} · {row.get('Steel User ID', '—')} · "
            f"{row.get('Steel Grade', '—')}"
        )
        for _, row in recent.iterrows()
        if pd.notna(row.get("Run ID"))
    }
    recent_run_id = st.selectbox(
        "Run 선택 (최신 200건)", ids, format_func=lambda value: labels[value]
    )
    direct_id = int(
        st.number_input(
            "오래된 Run ID 직접 입력 (0이면 위 선택 사용)",
            min_value=0,
            value=0,
            step=1,
        )
    )
    run_id = direct_id or recent_run_id
    record = load_run_record(context.client, int(run_id))
    if not record:
        st.warning("선택한 Run을 찾지 못했습니다.")
        return
    data = record.get("data_json") or {}
    metadata = {
        "Run ID": record.get("id"),
        "Source": record.get("source"),
        "Process Type": record.get("process_type")
        or data.get("Run Information > Process Type"),
        "Sender": record.get("sender_name"),
        "Sender Email": record.get("sender_email"),
        "Steel User ID": record.get("steel_user_id"),
        "Run Date": record.get("run_date"),
        "Status": record.get("status"),
    }
    st.dataframe(pd.DataFrame([metadata]), use_container_width=True, hide_index=True)
    for section in (
        "Run Information",
        "Simulation Settings",
        "Cost Breakdown",
        "Steel Composition",
        "Raw Materials",
        "Additions",
        "Slag Composition",
    ):
        st.markdown(f"#### {section}")
        section_data = _section_frame(data, section)
        if section_data.empty:
            st.caption("해당 섹션 데이터가 없습니다.")
        else:
            st.dataframe(section_data, use_container_width=True, hide_index=True)
    features = {
        key: value for key, value in data.items() if str(key).startswith("feature >")
    }
    st.markdown("#### ML Features")
    st.dataframe(
        pd.DataFrame(
            [{"Feature": key, "Value": value} for key, value in features.items()]
        ),
        use_container_width=True,
        hide_index=True,
    )
    logs = load_logs_df(context.client, run_ids=[int(run_id)], limit=1000)
    st.markdown("#### Event Log")
    if logs.empty:
        st.caption("Event Log가 없습니다.")
        return
    log_columns = [
        column
        for column in ("event_time", "event_seconds", "category", "event")
        if column in logs
    ]
    st.dataframe(logs[log_columns], use_container_width=True, hide_index=True)
    st.plotly_chart(
        process_timeline(logs, f"Run #{run_id} 공정 Timeline"),
        use_container_width=True,
    )


def render_visualizations(context: AuthContext) -> None:
    st.subheader("시각화")
    frame = load_runs_df(context.client, limit=1000, filters=_filters("viz"))
    st.caption("성능을 위해 현재 필터의 최신 Run을 최대 1,000건까지 시각화합니다.")
    if frame.empty:
        st.info("시각화할 Run이 없습니다.")
        return
    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            line_by_run_date(frame, "Cost Per Tonne", "1. Run Date vs Cost Per Tonne"),
            use_container_width=True,
        )
        st.plotly_chart(histogram(frame, "Score", "3. Score distribution"), use_container_width=True)
        st.plotly_chart(
            scatter(frame, "Score", "Cost Per Tonne", "5. Score vs Cost Per Tonne"),
            use_container_width=True,
        )
        st.plotly_chart(
            grouped_average(frame, "Steel Grade", "Time", "7. Steel Grade별 평균 Process Time"),
            use_container_width=True,
        )
        st.plotly_chart(
            grouped_average(frame, "Steel User ID", "Cost Per Tonne", "9. 사용자별 평균 Cost Per Tonne"),
            use_container_width=True,
        )
    with right:
        st.plotly_chart(
            line_by_run_date(frame, "Time", "2. Run Date vs Process Time"),
            use_container_width=True,
        )
        st.plotly_chart(
            histogram(frame, "Cost Per Tonne", "4. Cost Per Tonne distribution"),
            use_container_width=True,
        )
        st.plotly_chart(
            grouped_average(frame, "Steel Grade", "Cost Per Tonne", "6. Steel Grade별 평균 Cost"),
            use_container_width=True,
        )
        st.plotly_chart(
            grouped_count(frame, "Steel User ID", "8. 사용자별 Run 수"),
            use_container_width=True,
        )
        features = get_feature_columns(frame)
        if features:
            feature = st.selectbox("Feature 선택", features, key="viz_feature")
            target = st.radio(
                "비교 목표", ["Score", "Cost Per Tonne"], horizontal=True
            )
            st.plotly_chart(
                feature_scatter(frame, feature, target), use_container_width=True
            )
        else:
            st.info("10. Feature scatter를 그릴 feature가 없습니다.")
    st.markdown("#### 11. ML Feature Importance")
    result = st.session_state.get("ml_result")
    if result and result.get("importance") is not None:
        importance = result["importance"].head(20)
        st.plotly_chart(
            px.bar(importance, x="Importance", y="Feature", orientation="h"),
            use_container_width=True,
        )
    else:
        st.info("ML 분석 페이지에서 모델을 학습하면 Feature Importance가 표시됩니다.")


def _targets_nearly_equal(frame: pd.DataFrame) -> bool:
    score = _numeric(frame, "Score")
    cost = _numeric(frame, "Cost Per Tonne")
    valid = score.notna() & cost.notna()
    return bool(
        valid.any()
        and np.allclose(score[valid], cost[valid], rtol=1e-5, atol=1e-5)
    )


def render_ml(context: AuthContext) -> None:
    st.subheader("ML 분석")
    target = st.selectbox("예측 목표", ["Cost Per Tonne", "Score", "Time"])
    st.caption("학습은 버튼을 누를 때만 실행되며 최대 10,000개 Run을 사용합니다.")
    if st.button("모델 학습", type="primary"):
        with st.spinner("데이터를 불러와 모델을 학습하는 중입니다..."):
            frame = load_all_runs_df(context.client, max_rows=10000)
            model, features, importance, metrics = train_model(frame, target)
            st.session_state["ml_result"] = {
                "model": model,
                "features": features,
                "importance": importance,
                "metrics": metrics,
                "runs": frame,
                "target": target,
            }
    result = st.session_state.get("ml_result")
    if not result:
        st.info("20개 이상의 Run이 쌓인 뒤 모델 학습을 실행하세요.")
        return
    if result.get("target") != target:
        st.info("선택한 목표로 다시 학습 버튼을 눌러 주세요.")
        return
    metrics = result["metrics"]
    if metrics.get("error"):
        st.warning(metrics["error"])
        return
    if metrics.get("warning"):
        st.warning(metrics["warning"])
    if metrics.get("Excluded Merged Runs"):
        st.caption(
            f"병합 의심 Run {int(metrics['Excluded Merged Runs'])}건을 학습에서 제외했습니다."
        )
    frame = result["runs"]
    if _targets_nearly_equal(frame):
        st.warning(
            "현재 데이터에서는 Score와 Cost Per Tonne이 같거나 거의 같아 중복 목표일 수 있습니다."
        )
    for column, key in zip(
        st.columns(5), ["R2", "MAE", "RMSE", "Train Count", "Feature Count"]
    ):
        decimals = 4 if key in {"R2", "MAE", "RMSE"} else 0
        column.metric(key, _metric_text(metrics.get(key), decimals))
    importance = result["importance"]
    st.markdown("#### Feature Importance")
    st.plotly_chart(
        px.bar(importance.head(25), x="Importance", y="Feature", orientation="h"),
        use_container_width=True,
    )
    st.dataframe(importance, use_container_width=True, hide_index=True)
    uncertainty = estimate_model_uncertainty(
        result["model"], frame, result["features"], target_name=target
    )
    st.markdown("#### Ensemble Uncertainty")
    if uncertainty is not None:
        st.dataframe(uncertainty.head(50), use_container_width=True, hide_index=True)
    if target == "Score":
        recommendations = generate_recommendations(frame, importance)
        if recommendations:
            st.markdown("#### 관찰된 연관 방향")
            st.dataframe(
                pd.DataFrame(recommendations), use_container_width=True, hide_index=True
            )


def render_active_learning(context: AuthContext) -> None:
    st.subheader("Active Learning")
    st.warning(
        "현재 추천은 파이프라인 검증용이며 실제 조업 최적 조건을 의미하지 않습니다."
    )
    result = st.session_state.get("ml_result")
    if not result or result.get("model") is None:
        st.info("먼저 ML 분석 페이지에서 모델을 학습하세요.")
        return
    frame = result["runs"]
    uncertainty = estimate_model_uncertainty(
        result["model"],
        frame,
        result["features"],
        target_name=result["target"],
    )
    st.markdown("#### 모델이 잘 모르는 기존 Run")
    if uncertainty is not None:
        st.dataframe(uncertainty.head(30), use_container_width=True, hide_index=True)
    count = st.slider("생성할 후보 탐색 수", 100, 2000, 500, 100)
    if st.button("안전 범위 내 실험 후보 생성"):
        candidates = generate_candidates(
            result["model"],
            frame,
            result["features"],
            candidate_count=count,
            top_n=30,
        )
        if candidates.empty:
            st.info("변화가 있는 controllable feature가 부족해 후보를 만들지 못했습니다.")
        else:
            st.caption(
                "관측된 controllable feature의 최소·최대 범위 안에서만 생성했습니다."
            )
            st.dataframe(candidates, use_container_width=True, hide_index=True)


def render_manual_upload(context: AuthContext) -> None:
    st.subheader("수동 DOCX 업로드")
    st.caption(
        "DOCX 내부 User ID와 로그인 사용자의 sender 이메일은 별도 필드로 저장됩니다."
    )
    files = st.file_uploader(
        "Steel Challenge 결과 DOCX",
        type=["docx"],
        accept_multiple_files=True,
    )
    if not st.button("업로드 파일 저장", type="primary"):
        return
    if not files:
        st.warning("DOCX 파일을 선택하세요.")
        return
    success = 0
    for uploaded in files:
        try:
            parsed = parse_docx_canonical(uploaded)
            sender_name = context.profile.get("display_name") or context.user.email
            save_to_db(
                str(sender_name),
                uploaded.name,
                parsed.data,
                parsed.logs,
                client=context.client,
                source="docx",
                sender_name=str(sender_name),
                sender_email=str(context.user.email or ""),
            )
            success += 1
            if parsed.warnings:
                st.warning(f"{uploaded.name}: " + " / ".join(parsed.warnings))
        except Exception as exc:
            LOGGER.exception("DOCX upload failed: %s", uploaded.name)
            st.error(f"{uploaded.name} 처리 실패: {exc}")
    if success:
        st.success(f"{success}개 파일을 저장했습니다.")


def _build_download(
    context: AuthContext, filters: Optional[Mapping[str, Any]]
) -> tuple[bytes, int, int, str, dict[str, int]]:
    if not context.profile.get("public_access"):
        assert_can_download(context.client, str(context.user.id))
    runs = load_all_runs_df(context.client, filters=filters, max_rows=10000)
    run_ids = [
        int(value)
        for value in runs.get("Run ID", pd.Series(dtype="int64")).dropna()
    ]
    logs = load_all_logs_df(context.client, run_ids=run_ids, max_rows=100000)
    results_frame, analysis_frame = build_excel_frames(runs, logs)
    summary = summarize_export_frames(results_frame, analysis_frame)
    timestamp = datetime.now(SEOUL).strftime("%Y%m%d_%H%M%S")
    file_name = f"SteelChallenge_Master_{timestamp}.xlsx"
    return (
        make_excel_from_frames(results_frame, analysis_frame).getvalue(),
        len(runs),
        len(logs),
        file_name,
        summary,
    )


def render_download(context: AuthContext) -> None:
    st.subheader("다운로드")
    if context.profile.get("public_access"):
        st.info("공개 조회 모드에서 Excel 다운로드가 활성화되어 있습니다.")
    else:
        st.info("권한은 Excel 생성 직전에 서버에서 다시 확인합니다.")
    filters = _filters("download")
    left, right = st.columns(2)
    with left:
        if st.button("현재 필터 결과 Excel 생성", use_container_width=True):
            with st.spinner("Excel 생성 중..."):
                try:
                    st.session_state["filtered_export"] = _build_download(
                        context, filters
                    )
                except ValueError as exc:
                    st.session_state.pop("filtered_export", None)
                    st.error(str(exc))
        if "filtered_export" in st.session_state:
            data, run_count, log_count, file_name, summary = st.session_state["filtered_export"]
            st.caption(
                f"RESULTS {summary['results_columns']} / ANALYSIS {summary['analysis_columns']} columns · "
                f"RM log 복원 {summary['rm_reconstructed']} · "
                f"Addition log 복원 {summary['add_reconstructed']} · "
                f"Cost mapping 경고 {summary['cost_mapping_warnings']} · "
                f"병합 의심 {summary['merged_suspects']}"
            )
            st.download_button(
                f"현재 필터 다운로드 ({run_count} Runs / {log_count} Logs)",
                data,
                file_name,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    with right:
        if st.button("전체 데이터 Excel 생성", use_container_width=True):
            with st.spinner("전체 Excel 생성 중..."):
                try:
                    st.session_state["full_export"] = _build_download(context, None)
                except ValueError as exc:
                    st.session_state.pop("full_export", None)
                    st.error(str(exc))
        if "full_export" in st.session_state:
            data, run_count, log_count, file_name, summary = st.session_state["full_export"]
            st.caption(
                f"RESULTS {summary['results_columns']} / ANALYSIS {summary['analysis_columns']} columns · "
                f"RM report {summary['rm_report']} / "
                f"log 복원 {summary['rm_reconstructed']} · Addition report {summary['add_report']} / "
                f"log 복원 {summary['add_reconstructed']} · Cost mapping 경고 "
                f"{summary['cost_mapping_warnings']} · 병합 의심 {summary['merged_suspects']}"
            )
            st.download_button(
                f"전체 다운로드 ({run_count} Runs / {log_count} Logs)",
                data,
                file_name,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    st.caption(
        "안전 상한: Runs 10,000건, Event Logs 100,000건. 그 이상은 DB export를 사용하세요."
    )


def render_data_quality(context: AuthContext) -> None:
    user_id = str(context.user.id)
    assert_admin(context.client, user_id)
    st.subheader("데이터 품질")
    try:
        metrics = load_data_quality_metrics(context.client, user_id)
    except Exception:
        LOGGER.exception("data_quality_metrics RPC failed")
        metrics = {}
        st.warning("최신 데이터 품질 RPC를 찾지 못했습니다. schema.sql을 다시 적용하세요.")
    labels = [
        ("총 이메일 수", "total_emails"),
        ("파싱 성공", "parsed_success"),
        ("파싱 실패", "parsed_failed"),
        ("중복 skip", "duplicate_skips"),
        ("Run 저장 수", "saved_runs"),
        ("Event Log 없는 Run", "runs_without_logs"),
        ("Steel Grade missing", "missing_steel_grade"),
        ("Run Date missing", "missing_run_date"),
    ]
    columns = st.columns(4) + st.columns(4)
    for column, (label, key) in zip(columns, labels):
        column.metric(label, f"{int(metrics.get(key) or 0):,}")

    if st.button("Run 품질 진단 계산", use_container_width=True):
        with st.spinner("Run과 Event Log를 교차 검증하는 중입니다..."):
            runs = load_all_runs_df(context.client, max_rows=10000)
            run_ids = [
                int(value)
                for value in runs.get("Run ID", pd.Series(dtype="int64")).dropna()
            ]
            logs = load_all_logs_df(context.client, run_ids=run_ids, max_rows=100000)
            st.session_state["run_quality_summary"] = summarize_export_frame(
                build_all_runs_export_df(runs, logs)
            )
    quality_summary = st.session_state.get("run_quality_summary")
    if quality_summary:
        quality_labels = [
            ("병합 의심", "merged_suspects"),
            ("RM report", "rm_report"),
            ("RM log 복원", "rm_reconstructed"),
            ("Addition report", "add_report"),
            ("Addition log 복원", "add_reconstructed"),
            ("Cost mapping 경고", "cost_mapping_warnings"),
            ("경고 Run", "warning_runs"),
            ("Export columns", "columns"),
        ]
        for column, (label, key) in zip(st.columns(4) + st.columns(4), quality_labels):
            column.metric(label, f"{int(quality_summary.get(key) or 0):,}")
    emails = load_email_messages_df(context.client, user_id, limit=1000)
    st.markdown("#### 파싱 실패 이메일")
    if emails.empty or "parsed_status" not in emails:
        st.caption("수집된 이메일이 없습니다.")
        return
    failed = emails[emails["parsed_status"] == "failed"]
    visible = [
        column
        for column in (
            "gmail_message_id",
            "sender_name",
            "sender_email",
            "subject",
            "email_received_at",
            "parser_error",
        )
        if column in failed
    ]
    st.dataframe(failed[visible], use_container_width=True, hide_index=True)


def render_user_management(context: AuthContext) -> None:
    user_id = str(context.user.id)
    assert_admin(context.client, user_id)
    st.subheader("사용자 관리")
    profiles = load_profiles_df(context.client, user_id)
    visible = [
        column
        for column in (
            "email",
            "display_name",
            "approved",
            "can_view",
            "can_download",
            "is_admin",
            "created_at",
        )
        if column in profiles
    ]
    st.dataframe(profiles[visible], use_container_width=True, hide_index=True)
    if not profiles.empty:
        options = profiles["id"].astype(str).tolist()
        labels = {
            str(row["id"]): f"{row.get('email', '')} · {row.get('display_name', '')}"
            for _, row in profiles.iterrows()
        }
        selected_id = st.selectbox(
            "수정할 사용자", options, format_func=lambda value: labels[value]
        )
        selected = profiles[profiles["id"].astype(str) == selected_id].iloc[0]
        with st.form("permission_form"):
            display_name = st.text_input(
                "Display Name", value=str(selected.get("display_name") or "")
            )
            approved = st.checkbox(
                "Approved", value=bool(selected.get("approved"))
            )
            can_view = st.checkbox("Can View", value=bool(selected.get("can_view")))
            can_download = st.checkbox(
                "Can Download", value=bool(selected.get("can_download"))
            )
            st.text_input(
                "Is Admin (읽기 전용)",
                value=str(bool(selected.get("is_admin"))),
                disabled=True,
            )
            save = st.form_submit_button("권한 저장", type="primary")
        if save:
            update_profile_permissions(
                context.client,
                user_id,
                selected_id,
                display_name=display_name,
                approved=approved,
                can_view=can_view,
                can_download=can_download,
            )
            st.success("권한을 저장했습니다.")
            st.rerun()

    st.markdown("#### Auth 사용자 초대")
    if not get_supabase_service_key(required=False):
        st.info(
            "SUPABASE_SERVICE_ROLE_KEY가 서버에 설정된 경우에만 초대 기능이 활성화됩니다."
        )
        return
    with st.form("invite_form"):
        invite_email = st.text_input("초대 이메일")
        invite_name = st.text_input("표시 이름")
        redirect_url = st.text_input("초대 후 이동 URL (선택)")
        submitted = st.form_submit_button("초대 메일 보내기")
    if submitted:
        if not invite_email.strip():
            st.warning("초대 이메일을 입력하세요.")
            return
        invite_user(
            context.client,
            user_id,
            invite_email,
            display_name=invite_name,
            redirect_to=redirect_url,
        )
        st.success("Supabase Auth 초대 요청을 보냈습니다.")


PAGE_RENDERERS = {
    "Dashboard": render_dashboard,
    "전체 Run": render_all_runs,
    "Run 상세": render_run_detail,
    "시각화": render_visualizations,
    "ML 분석": render_ml,
    "Active Learning": render_active_learning,
    "수동 업로드": render_manual_upload,
    "다운로드": render_download,
    "데이터 품질": render_data_quality,
    "사용자 관리": render_user_management,
}


def render_page(name: str, context: AuthContext) -> None:
    renderer = PAGE_RENDERERS.get(name)
    if renderer is None:
        st.error("알 수 없는 페이지입니다.")
        return
    renderer(context)
