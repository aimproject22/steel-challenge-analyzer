# -*- coding: utf-8 -*-
"""Leakage-aware RandomForest utilities with backward-compatible wrappers."""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split


MIN_TRAINING_ROWS = 20
SUPPORTED_TARGETS = ("Cost Per Tonne", "Score", "Time")


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    return [column for column in df.columns if str(column).startswith("feature >")]


def _leakage_tokens(target_col: str) -> tuple[str, ...]:
    if target_col == "Cost Per Tonne":
        return ("cost", "score", "total_cost")
    if target_col == "Score":
        return ("score", "cost")
    if target_col == "Time":
        return (
            "tapping_complete",
            "tapping_duration",
            "process_time",
            "time_minutes",
        )
    return ()


def get_safe_feature_columns(df: pd.DataFrame, target_col: str) -> list[str]:
    tokens = _leakage_tokens(target_col)
    result = []
    for column in get_feature_columns(df):
        lowered = str(column).casefold()
        if any(token in lowered for token in tokens):
            continue
        result.append(column)
    return result


def train_model(
    runs_df: pd.DataFrame,
    target_col: str = "Cost Per Tonne",
) -> tuple[Optional[RandomForestRegressor], Optional[list[str]], Optional[pd.DataFrame], dict]:
    if target_col not in SUPPORTED_TARGETS:
        return None, None, None, {"error": f"지원하지 않는 목표입니다: {target_col}"}
    if len(runs_df) < MIN_TRAINING_ROWS:
        return None, None, None, {
            "error": f"데이터가 {MIN_TRAINING_ROWS}개 미만이라 모델 학습이 어렵습니다."
        }
    if target_col not in runs_df.columns:
        return None, None, None, {"error": f"{target_col} 컬럼이 없습니다."}

    feature_cols = get_safe_feature_columns(runs_df, target_col)
    if not feature_cols:
        return None, None, None, {"error": "누출을 제외한 feature 컬럼이 없습니다."}

    X = runs_df[feature_cols].apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(runs_df[target_col], errors="coerce")
    valid = y.notna()
    X = X.loc[valid].reset_index(drop=True)
    y = y.loc[valid].reset_index(drop=True)
    if len(X) < MIN_TRAINING_ROWS:
        return None, None, None, {"error": "유효한 학습 데이터가 부족합니다."}

    # Remove unusable features before splitting. No target values are consulted.
    usable = [column for column in X.columns if X[column].notna().any()]
    X = X[usable]
    if X.empty:
        return None, None, None, {"error": "수치로 사용할 수 있는 feature가 없습니다."}

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.25,
        random_state=42,
    )
    medians = X_train.median(numeric_only=True).fillna(0.0)
    X_train = X_train.fillna(medians)
    X_test = X_test.fillna(medians)

    non_constant = [column for column in X_train.columns if X_train[column].nunique() > 1]
    if not non_constant:
        return None, None, None, {"error": "변화가 있는 feature가 없습니다."}
    X_train = X_train[non_constant]
    X_test = X_test[non_constant]
    medians = medians[non_constant]

    model = RandomForestRegressor(
        n_estimators=300,
        random_state=42,
        min_samples_leaf=2,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    prediction = model.predict(X_test)

    r2 = r2_score(y_test, prediction) if len(y_test) >= 2 else float("nan")
    metrics = {
        "Target": target_col,
        "R2": None if math.isnan(r2) else round(float(r2), 4),
        "MAE": round(float(mean_absolute_error(y_test, prediction)), 4),
        "RMSE": round(float(math.sqrt(mean_squared_error(y_test, prediction))), 4),
        "Train Count": len(X_train),
        "Test Count": len(X_test),
        "Feature Count": len(non_constant),
    }
    importance_df = pd.DataFrame(
        {"Feature": non_constant, "Importance": model.feature_importances_}
    ).sort_values("Importance", ascending=False, ignore_index=True)

    # Store training preprocessing on the model so inference cannot recalculate it.
    model._steel_feature_medians = medians.to_dict()  # type: ignore[attr-defined]
    model._steel_target = target_col  # type: ignore[attr-defined]
    return model, non_constant, importance_df, metrics


def train_score_model(runs_df: pd.DataFrame):
    """Existing API retained for callers that train only the Score target."""

    return train_model(runs_df, "Score")


def generate_recommendations(runs_df: pd.DataFrame, importance_df: pd.DataFrame):
    """Describe observed associations; do not present them as causal settings."""

    if importance_df is None or importance_df.empty or "Score" not in runs_df:
        return []

    numeric_score = pd.to_numeric(runs_df["Score"], errors="coerce")
    minimize = False
    if "Cost Per Tonne" in runs_df:
        numeric_cost = pd.to_numeric(runs_df["Cost Per Tonne"], errors="coerce")
        comparable = numeric_score.notna() & numeric_cost.notna()
        if comparable.any():
            minimize = bool(
                np.allclose(
                    numeric_score[comparable],
                    numeric_cost[comparable],
                    rtol=1e-5,
                    atol=1e-5,
                )
            )

    work = runs_df.assign(_score=numeric_score).dropna(subset=["_score"])
    top_n = max(5, len(work) // 5)
    reference_runs = work.sort_values("_score", ascending=minimize).head(top_n)
    recommendations = []
    for feature in importance_df.head(8)["Feature"].tolist():
        if feature not in work:
            continue
        all_average = pd.to_numeric(work[feature], errors="coerce").mean()
        reference_average = pd.to_numeric(reference_runs[feature], errors="coerce").mean()
        if pd.isna(all_average) or pd.isna(reference_average):
            continue
        direction = "높은" if reference_average > all_average else "낮은"
        recommendations.append(
            {
                "Feature": feature,
                "All Average": round(float(all_average), 3),
                "Reference Average": round(float(reference_average), 3),
                "Observed Direction": direction,
                "Comment": (
                    f"{feature}는 기준 Run 집단에서 전체 평균보다 {direction} 값이 관찰됐습니다. "
                    "인과관계나 조업 지시를 의미하지 않습니다."
                ),
            }
        )
    return recommendations


def _prepared_features(
    model: RandomForestRegressor,
    runs_df: pd.DataFrame,
    feature_cols: list[str],
) -> pd.DataFrame:
    X = runs_df.reindex(columns=feature_cols).apply(pd.to_numeric, errors="coerce")
    medians = getattr(model, "_steel_feature_medians", {})
    for column in feature_cols:
        X[column] = X[column].fillna(float(medians.get(column, 0.0)))
    return X.reset_index(drop=True)


def estimate_model_uncertainty(
    model: Optional[RandomForestRegressor],
    runs_df: pd.DataFrame,
    feature_cols: list[str],
    *,
    target_name: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    if model is None or runs_df.empty:
        return None
    X = _prepared_features(model, runs_df, feature_cols)
    tree_predictions = np.vstack([tree.predict(X) for tree in model.estimators_]).T
    target = target_name or getattr(model, "_steel_target", "Target")

    result = pd.DataFrame(
        {
            "Run ID": runs_df.get("Run ID", pd.Series(range(len(runs_df)))).reset_index(drop=True),
            "File Name": runs_df.get("File Name", pd.Series([None] * len(runs_df))).reset_index(
                drop=True
            ),
            "Prediction Mean": tree_predictions.mean(axis=1),
            "Prediction Std": tree_predictions.std(axis=1),
            "Target": target,
        }
    )
    result["Uncertainty"] = result["Prediction Std"]
    if target == "Score":
        result["Predicted Score"] = result["Prediction Mean"]
    return result.sort_values("Prediction Std", ascending=False, ignore_index=True)


def estimate_uncertainty(model, runs_df, feature_cols):
    """Existing Score uncertainty API retained."""

    return estimate_model_uncertainty(
        model,
        runs_df,
        feature_cols,
        target_name="Score",
    )
