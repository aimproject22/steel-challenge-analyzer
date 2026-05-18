# db_utils.py
# -*- coding: utf-8 -*-

import pandas as pd
import streamlit as st
from supabase import create_client

from feature_engineering import extract_features_from_logs


SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def init_db():
    """
    Supabase에서는 테이블을 SQL Editor에서 이미 생성했기 때문에
    여기서는 별도 작업을 하지 않음.
    """
    return None


def save_to_db(uploader, file_name, data, logs):
    features = extract_features_from_logs(logs)

    merged_data = {}
    merged_data.update(data)
    merged_data.update(features)

    score = data.get("Run Information > Score")
    cost_per_tonne = data.get("Cost Breakdown > Cost Per Tonne")
    steel_grade = data.get("Simulation Settings > Steel Grade")

    run_data = {
        "uploader": uploader,
        "file_name": file_name,
        "score": score,
        "cost_per_tonne": cost_per_tonne,
        "steel_grade": steel_grade,
        "data_json": merged_data,
    }

    run_response = (
        supabase
        .table("runs")
        .insert(run_data)
        .execute()
    )

    if not run_response.data:
        raise RuntimeError("Supabase runs 테이블 저장 실패")

    run_id = run_response.data[0]["id"]

    log_rows = []

    for log in logs:
        log_rows.append({
            "run_id": run_id,
            "log_no": log["log_no"],
            "time": log["time"],
            "event": log["event"],
        })

    if log_rows:
        supabase.table("logs").insert(log_rows).execute()


def load_runs_df():
    response = (
        supabase
        .table("runs")
        .select("*")
        .order("id", desc=True)
        .execute()
    )

    if not response.data:
        return pd.DataFrame()

    rows = []

    for row in response.data:
        data = row.get("data_json") or {}

        new_row = {
            "Run ID": row.get("id"),
            "Uploaded At": row.get("uploaded_at"),
            "Uploader": row.get("uploader"),
            "File Name": row.get("file_name"),
            "Score": row.get("score"),
            "Cost Per Tonne": row.get("cost_per_tonne"),
            "Steel Grade": row.get("steel_grade"),
        }

        new_row.update(data)
        rows.append(new_row)

    return pd.DataFrame(rows)


def load_logs_df():
    response = (
        supabase
        .table("logs")
        .select("*")
        .order("run_id", desc=True)
        .order("log_no", desc=False)
        .execute()
    )

    if not response.data:
        return pd.DataFrame()

    return pd.DataFrame(response.data)


def reset_database():
    supabase.table("logs").delete().neq("id", 0).execute()
    supabase.table("runs").delete().neq("id", 0).execute()