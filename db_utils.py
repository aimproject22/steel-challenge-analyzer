# -*- coding: utf-8 -*-
"""Supabase repository functions.

No authenticated client is stored globally. Every caller passes a client tied
to the current Streamlit session, while email ingestion passes a service client.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

import pandas as pd

from canonical import normalize_datetime, normalize_event_log
from feature_engineering import extract_features_from_logs


FEATURE_SCHEMA_VERSION = 2
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000


def init_db() -> None:
    """Compatibility no-op; schema changes live in ``supabase/*.sql``."""

    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> Optional[int]:
    number = _number(value)
    return None if number is None else int(number)


def _iso_run_date(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    parsed = normalize_datetime(value)
    return parsed.isoformat() if parsed else None


def build_run_payload(
    *,
    uploader: str,
    file_name: str,
    data: Mapping[str, Any],
    logs: Iterable[Mapping[str, Any]],
    source: str = "docx",
    sender_name: Optional[str] = None,
    sender_email: Optional[str] = None,
    email_message_id: Optional[int] = None,
    source_run_index: int = 1,
) -> tuple[dict, list[dict]]:
    normalized_logs = [
        normalize_event_log(log, index)
        for index, log in enumerate(logs or [], start=1)
    ]
    features = extract_features_from_logs(normalized_logs, data)
    merged_data = dict(data)
    merged_data.update(features)
    merged_data["feature_schema_version"] = FEATURE_SCHEMA_VERSION

    run_payload = {
        "email_message_id": email_message_id,
        "source_run_index": max(1, int(source_run_index)),
        "source": source,
        "sender_name": sender_name or uploader,
        "sender_email": sender_email,
        "steel_user_id": data.get("Run Information > User Id"),
        "process_type": data.get("Run Information > Process Type"),
        "run_date": _iso_run_date(data.get("Run Information > Date")),
        "status": _integer(data.get("Run Information > Status")),
        "score": _number(data.get("Run Information > Score")),
        "user_level": data.get("Simulation Settings > User Level"),
        "steel_grade": data.get("Simulation Settings > Steel Grade"),
        "time_minutes": _number(data.get("Cost Breakdown > Time (in minutes)")),
        "tapping_mass": _number(data.get("Cost Breakdown > Tapping mass")),
        "tap_temperature": _number(data.get("Cost Breakdown > Tap temperature")),
        "total_energy_kwh": _number(data.get("Cost Breakdown > Total Energy")),
        "energy_kwh_per_t": _number(data.get("Cost Breakdown > Total Energy_2")),
        "power_cost": _number(data.get("Cost Breakdown > Power")),
        "scrap_cost": _number(data.get("Cost Breakdown > Scrap")),
        "additions_cost": _number(data.get("Cost Breakdown > Additions")),
        "other_consumables_cost": _number(
            data.get("Cost Breakdown > Other consumables")
        ),
        "total_cost": _number(data.get("Cost Breakdown > Total Cost")),
        "cost_per_tonne": _number(data.get("Cost Breakdown > Cost Per Tonne")),
        "uploader": uploader,
        "file_name": file_name,
        "data_json": _json_safe(merged_data),
    }
    log_payload = [_json_safe(log) for log in normalized_logs]
    return _json_safe(run_payload), log_payload


def save_to_db(
    uploader: str,
    file_name: str,
    data: Mapping[str, Any],
    logs: Iterable[Mapping[str, Any]],
    *,
    client: Any,
    source: str = "docx",
    sender_name: Optional[str] = None,
    sender_email: Optional[str] = None,
    email_message_id: Optional[int] = None,
    source_run_index: int = 1,
) -> int:
    """Save one run and its logs atomically through the database function."""

    if client is None:
        raise ValueError("세션별 Supabase client가 필요합니다.")
    run_payload, log_payload = build_run_payload(
        uploader=uploader,
        file_name=file_name,
        data=data,
        logs=logs,
        source=source,
        sender_name=sender_name,
        sender_email=sender_email,
        email_message_id=email_message_id,
        source_run_index=source_run_index,
    )
    response = client.rpc(
        "save_run_with_logs",
        {"run_payload": run_payload, "log_payload": log_payload},
    ).execute()
    result = response.data
    if isinstance(result, list) and result:
        result = result[0]
    if isinstance(result, dict):
        result = result.get("save_run_with_logs") or result.get("id")
    if result is None:
        raise RuntimeError("Supabase run/log 저장에 실패했습니다.")
    return int(result)


def replace_email_runs_with_logs(
    client: Any,
    email_message_id: int,
    entries: Sequence[Mapping[str, Any]],
) -> list[int]:
    """Atomically replace every parsed run belonging to one Gmail message."""

    if client is None:
        raise ValueError("Supabase service client가 필요합니다.")
    if not entries:
        raise ValueError("저장할 Gmail Run이 없습니다.")
    payload = []
    for entry in entries:
        run_payload = dict(entry.get("run_payload") or {})
        log_payload = list(entry.get("log_payload") or [])
        run_payload["email_message_id"] = int(email_message_id)
        payload.append(
            {
                "run_payload": _json_safe(run_payload),
                "log_payload": _json_safe(log_payload),
            }
        )
    response = client.rpc(
        "replace_email_runs_with_logs",
        {
            "p_email_message_id": int(email_message_id),
            "run_entries": payload,
        },
    ).execute()
    result = response.data
    if isinstance(result, list) and len(result) == 1:
        if isinstance(result[0], Mapping):
            result = (
                result[0].get("replace_email_runs_with_logs")
                or result[0].get("ids")
            )
        elif isinstance(result[0], list):
            result = result[0]
    if isinstance(result, Mapping):
        result = result.get("replace_email_runs_with_logs") or result.get("ids")
    if isinstance(result, str):
        result = [item for item in result.strip("{}").split(",") if item]
    if not isinstance(result, list) or not result:
        raise RuntimeError("Supabase Gmail Run 일괄 저장에 실패했습니다.")
    return [int(item) for item in result]


def _fallback(data: Mapping[str, Any], direct: str, flat: str) -> Any:
    direct_value = data.get(direct)
    if direct_value is not None:
        return direct_value
    payload = data.get("data_json") or {}
    return payload.get(flat) if isinstance(payload, Mapping) else None


def _run_row_to_display(row: Mapping[str, Any]) -> dict:
    payload = row.get("data_json") or {}
    display = {
        "Run ID": row.get("id"),
        "Run Date": _fallback(row, "run_date", "Run Information > Date"),
        "Uploaded At": row.get("uploaded_at") or row.get("created_at"),
        "Source": row.get("source") or "docx",
        "Source Run Index": row.get("source_run_index") or 1,
        "Sender": row.get("sender_name") or row.get("uploader"),
        "Sender Email": row.get("sender_email"),
        "Steel User ID": _fallback(row, "steel_user_id", "Run Information > User Id"),
        "Process Type": _fallback(
            row, "process_type", "Run Information > Process Type"
        ),
        "Uploader": row.get("uploader") or row.get("sender_name"),
        "File Name": row.get("file_name"),
        "Status": _fallback(row, "status", "Run Information > Status"),
        "Score": _fallback(row, "score", "Run Information > Score"),
        "User Level": _fallback(row, "user_level", "Simulation Settings > User Level"),
        "Steel Grade": _fallback(
            row, "steel_grade", "Simulation Settings > Steel Grade"
        ),
        "Time": _fallback(row, "time_minutes", "Cost Breakdown > Time (in minutes)"),
        "Tapping Mass": _fallback(
            row, "tapping_mass", "Cost Breakdown > Tapping mass"
        ),
        "Tap Temperature": _fallback(
            row, "tap_temperature", "Cost Breakdown > Tap temperature"
        ),
        "Total Energy kWh": _fallback(
            row, "total_energy_kwh", "Cost Breakdown > Total Energy"
        ),
        "Energy kWh/t": _fallback(
            row, "energy_kwh_per_t", "Cost Breakdown > Total Energy_2"
        ),
        "Total Cost": _fallback(row, "total_cost", "Cost Breakdown > Total Cost"),
        "Cost Per Tonne": _fallback(
            row, "cost_per_tonne", "Cost Breakdown > Cost Per Tonne"
        ),
    }
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if key not in display:
                display[key] = value
    return display


def _bounded_page_size(limit: int) -> int:
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def load_runs_df(
    client: Any,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    filters: Optional[Mapping[str, Any]] = None,
) -> pd.DataFrame:
    query = client.table("runs").select("*")
    active = dict(filters or {})
    if active.get("date_from"):
        query = query.gte("run_date", str(active["date_from"]))
    if active.get("date_to"):
        query = query.lte("run_date", str(active["date_to"]))
    for key in (
        "process_type",
        "steel_grade",
        "steel_user_id",
        "sender_email",
        "status",
    ):
        value = active.get(key)
        if value not in (None, "", "전체"):
            query = query.eq(key, value)
    page_size = _bounded_page_size(limit)
    response = (
        query.order("run_date", desc=True)
        .order("id", desc=True)
        .range(max(0, offset), max(0, offset) + page_size - 1)
        .execute()
    )
    return pd.DataFrame([_run_row_to_display(row) for row in (response.data or [])])


def load_all_runs_df(
    client: Any,
    *,
    filters: Optional[Mapping[str, Any]] = None,
    max_rows: int = 10000,
) -> pd.DataFrame:
    frames = []
    offset = 0
    while offset < max_rows:
        page_limit = min(MAX_PAGE_SIZE, max_rows - offset)
        frame = load_runs_df(client, limit=page_limit, offset=offset, filters=filters)
        if frame.empty:
            break
        frames.append(frame)
        if len(frame) < page_limit:
            break
        offset += page_limit
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_dashboard_metrics(
    client: Any,
    *,
    filters: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Return RLS-filtered aggregates without downloading every run."""

    active = dict(filters or {})
    params = {
        "p_date_from": active.get("date_from"),
        "p_date_to": active.get("date_to"),
        "p_process_type": active.get("process_type") or None,
        "p_steel_grade": active.get("steel_grade") or None,
        "p_steel_user_id": active.get("steel_user_id") or None,
        "p_sender_email": active.get("sender_email") or None,
        "p_status": active.get("status"),
    }
    response = client.rpc("dashboard_metrics", params).execute()
    result = response.data or []
    if isinstance(result, list):
        return dict(result[0]) if result else {}
    return dict(result) if isinstance(result, Mapping) else {}


def load_logs_df(
    client: Any,
    *,
    run_ids: Optional[Sequence[int]] = None,
    limit: int = 1000,
    offset: int = 0,
) -> pd.DataFrame:
    query = client.table("logs").select("*")
    if run_ids:
        query = query.in_("run_id", [int(value) for value in run_ids])
    page_size = _bounded_page_size(limit)
    response = (
        query.order("run_id", desc=True)
        .order("log_no", desc=False)
        .order("event_seconds", desc=False)
        .range(max(0, offset), max(0, offset) + page_size - 1)
        .execute()
    )
    return pd.DataFrame(response.data or [])


def load_all_logs_df(
    client: Any,
    *,
    run_ids: Optional[Sequence[int]] = None,
    max_rows: int = 100000,
) -> pd.DataFrame:
    """Page through logs and split large ID filters into safe request sizes."""

    if run_ids is not None and not run_ids:
        return pd.DataFrame()
    if run_ids is None:
        groups: list[Optional[list[int]]] = [None]
    else:
        values = [int(value) for value in run_ids]
        groups = [values[index : index + 100] for index in range(0, len(values), 100)]

    frames: list[pd.DataFrame] = []
    collected = 0
    for group in groups:
        offset = 0
        while collected < max_rows:
            page_limit = min(MAX_PAGE_SIZE, max_rows - collected)
            frame = load_logs_df(
                client,
                run_ids=group,
                limit=page_limit,
                offset=offset,
            )
            if frame.empty:
                break
            frames.append(frame)
            collected += len(frame)
            if len(frame) < page_limit:
                break
            offset += page_limit
        if collected >= max_rows:
            break
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_run_record(client: Any, run_id: int) -> Optional[dict]:
    response = client.table("runs").select("*").eq("id", int(run_id)).limit(1).execute()
    return dict(response.data[0]) if response.data else None


def load_profile(client: Any, user_id: str) -> dict:
    response = (
        client.table("profiles")
        .select("*")
        .eq("auth_user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return dict(response.data[0]) if response.data else {}


def assert_can_view(client: Any, user_id: str) -> dict:
    profile = load_profile(client, user_id)
    if not (profile.get("approved") and profile.get("can_view")):
        raise PermissionError("승인된 조회 권한이 필요합니다.")
    return profile


def assert_can_download(client: Any, user_id: str) -> dict:
    profile = assert_can_view(client, user_id)
    if not profile.get("can_download"):
        raise PermissionError("다운로드 권한이 필요합니다.")
    return profile


def assert_admin(client: Any, user_id: str) -> dict:
    profile = assert_can_view(client, user_id)
    if not profile.get("is_admin"):
        raise PermissionError("관리자 권한이 필요합니다.")
    return profile


def load_profiles_df(client: Any, user_id: str) -> pd.DataFrame:
    assert_admin(client, user_id)
    response = client.table("profiles").select("*").order("created_at").execute()
    return pd.DataFrame(response.data or [])


def update_profile_permissions(
    client: Any,
    current_user_id: str,
    profile_id: str,
    *,
    display_name: Optional[str],
    approved: bool,
    can_view: bool,
    can_download: bool,
) -> None:
    assert_admin(client, current_user_id)
    payload = {
        "display_name": display_name,
        "approved": bool(approved),
        "can_view": bool(can_view),
        "can_download": bool(can_download),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("profiles").update(payload).eq("id", profile_id).execute()


def load_email_messages_df(client: Any, user_id: str, *, limit: int = 500) -> pd.DataFrame:
    assert_admin(client, user_id)
    response = (
        client.table("email_messages")
        .select("*")
        .order("email_received_at", desc=True)
        .range(0, max(0, min(limit, 1000) - 1))
        .execute()
    )
    return pd.DataFrame(response.data or [])


def load_data_quality_metrics(client: Any, user_id: str) -> dict:
    assert_admin(client, user_id)
    response = client.rpc("data_quality_metrics", {}).execute()
    result = response.data or []
    if isinstance(result, list):
        return dict(result[0]) if result else {}
    return dict(result) if isinstance(result, Mapping) else {}


def get_email_message_by_gmail_id(client: Any, gmail_message_id: str) -> Optional[dict]:
    response = (
        client.table("email_messages")
        .select("*")
        .eq("gmail_message_id", gmail_message_id)
        .limit(1)
        .execute()
    )
    return dict(response.data[0]) if response.data else None


def get_email_messages_for_run_ids(
    client: Any, run_ids: Sequence[int]
) -> list[dict]:
    """Resolve stored Run IDs to their source Gmail message rows."""

    normalized = sorted({int(value) for value in run_ids})
    if not normalized:
        return []
    run_response = (
        client.table("runs")
        .select("email_message_id")
        .in_("id", normalized)
        .execute()
    )
    email_ids = sorted(
        {
            int(row["email_message_id"])
            for row in (run_response.data or [])
            if row.get("email_message_id") is not None
        }
    )
    if not email_ids:
        return []
    response = (
        client.table("email_messages")
        .select("*")
        .in_("id", email_ids)
        .execute()
    )
    return [dict(row) for row in (response.data or [])]


def create_email_message(client: Any, metadata: Mapping[str, Any]) -> dict:
    payload = {
        "gmail_message_id": metadata.get("gmail_message_id"),
        "gmail_thread_id": metadata.get("gmail_thread_id"),
        "sender_name": metadata.get("sender_name"),
        "sender_email": metadata.get("sender_email"),
        "subject": metadata.get("email_subject") or metadata.get("subject"),
        "email_received_at": metadata.get("email_received_at"),
        "parsed_status": metadata.get("parsed_status") or "processing",
        "parser_source": metadata.get("parser_source"),
        "parser_error": metadata.get("parser_error"),
        "raw_excerpt": str(metadata.get("raw_excerpt") or "")[:4000],
    }
    response = client.table("email_messages").insert(_json_safe(payload)).execute()
    if not response.data:
        raise RuntimeError("email_messages 저장에 실패했습니다.")
    return dict(response.data[0])


def update_email_message(client: Any, email_message_id: int, **updates: Any) -> None:
    allowed = {
        "parsed_status",
        "parser_source",
        "parser_error",
        "raw_excerpt",
        "duplicate_skip_count",
    }
    payload = {key: _json_safe(value) for key, value in updates.items() if key in allowed}
    if payload:
        client.table("email_messages").update(payload).eq("id", email_message_id).execute()


def increment_duplicate_skip(client: Any, email_row: Mapping[str, Any]) -> None:
    count = int(email_row.get("duplicate_skip_count") or 0) + 1
    update_email_message(client, int(email_row["id"]), duplicate_skip_count=count)


def reset_database(*args: Any, **kwargs: Any) -> None:
    """The unsafe prototype reset is intentionally unavailable in production."""

    raise PermissionError("운영 환경의 전체 데이터 초기화 기능은 제거되었습니다.")
