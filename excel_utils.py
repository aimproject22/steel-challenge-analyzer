"""Permission-neutral Excel generation helpers."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Optional

import pandas as pd


ILLEGAL_EXCEL_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")


def remove_illegal_characters(value):
    if not isinstance(value, str):
        return value
    cleaned = ILLEGAL_EXCEL_RE.sub("", value)
    # Prevent spreadsheet formula injection in exported user/email-controlled text.
    if cleaned.startswith(("=", "+", "-", "@")):
        cleaned = "'" + cleaned
    return cleaned


def clean_dataframe_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    for column in cleaned.select_dtypes(include=["object", "string"]).columns:
        cleaned[column] = cleaned[column].map(remove_illegal_characters)
    return cleaned


def feature_frame(runs_df: pd.DataFrame) -> pd.DataFrame:
    feature_columns = [
        column for column in runs_df.columns if str(column).startswith("feature >")
    ]
    identity_columns = [
        column
        for column in (
            "Run ID",
            "Run Date",
            "Sender",
            "Sender Email",
            "Steel User ID",
            "Steel Grade",
            "Score",
            "Cost Per Tonne",
            "Time",
        )
        if column in runs_df.columns
    ]
    return runs_df[identity_columns + feature_columns].copy()


def make_excel(
    runs_df: pd.DataFrame,
    logs_df: pd.DataFrame,
    ml_features_df: Optional[pd.DataFrame] = None,
) -> BytesIO:
    features = feature_frame(runs_df) if ml_features_df is None else ml_features_df
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        clean_dataframe_for_excel(runs_df).to_excel(
            writer, sheet_name="Runs", index=False
        )
        clean_dataframe_for_excel(logs_df).to_excel(
            writer, sheet_name="Event Logs", index=False
        )
        clean_dataframe_for_excel(features).to_excel(
            writer, sheet_name="ML Features", index=False
        )
    output.seek(0)
    return output

