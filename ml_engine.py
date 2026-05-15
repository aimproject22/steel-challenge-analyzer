# ml_engine.py
# -*- coding: utf-8 -*-

import pandas as pd

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error


def get_feature_columns(df):
    return [c for c in df.columns if str(c).startswith("feature >")]


def train_score_model(runs_df):
    target_col = "Score"
    feature_cols = get_feature_columns(runs_df)

    if len(runs_df) < 20:
        return None, None, None, {
            "error": "데이터가 20개 미만이라 모델 학습이 어렵습니다."
        }

    if not feature_cols:
        return None, None, None, {
            "error": "feature 컬럼이 없습니다."
        }

    if target_col not in runs_df.columns:
        return None, None, None, {
            "error": "Score 컬럼이 없습니다."
        }

    X = runs_df[feature_cols].copy()
    y = runs_df[target_col].copy()

    X = X.apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(y, errors="coerce")

    valid_idx = y.notna()
    X = X.loc[valid_idx]
    y = y.loc[valid_idx]

    X = X.fillna(X.median(numeric_only=True))

    if len(X) < 20:
        return None, None, None, {
            "error": "유효한 학습 데이터가 부족합니다."
        }

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.25,
        random_state=42
    )

    model = RandomForestRegressor(
        n_estimators=300,
        random_state=42,
        min_samples_leaf=2
    )

    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    metrics = {
        "R2": round(r2_score(y_test, pred), 4),
        "MAE": round(mean_absolute_error(y_test, pred), 4),
        "Train Count": len(X_train),
        "Test Count": len(X_test),
    }

    importance_df = pd.DataFrame({
        "Feature": feature_cols,
        "Importance": model.feature_importances_
    }).sort_values("Importance", ascending=False)

    return model, feature_cols, importance_df, metrics


def generate_recommendations(runs_df, importance_df):
    if importance_df is None or importance_df.empty:
        return []

    recommendations = []

    top_features = importance_df.head(8)["Feature"].tolist()

    top_n = max(5, len(runs_df) // 5)
    top_runs = runs_df.sort_values("Score", ascending=False).head(top_n)

    for feature in top_features:
        all_avg = pd.to_numeric(runs_df[feature], errors="coerce").mean()
        top_avg = pd.to_numeric(top_runs[feature], errors="coerce").mean()

        if pd.isna(all_avg) or pd.isna(top_avg):
            continue

        if top_avg > all_avg:
            direction = "높이는 방향"
        else:
            direction = "낮추는 방향"

        recommendations.append({
            "Feature": feature,
            "All Average": round(all_avg, 3),
            "High Score Average": round(top_avg, 3),
            "Suggested Direction": direction,
            "Comment": (
                f"{feature}는 고득점 Run에서 평균적으로 "
                f"{direction}의 경향을 보입니다."
            )
        })

    return recommendations


def estimate_uncertainty(model, runs_df, feature_cols):
    if model is None:
        return None

    X = runs_df[feature_cols].copy()
    X = X.apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median(numeric_only=True))

    tree_preds = []

    for tree in model.estimators_:
        tree_preds.append(tree.predict(X))

    pred_df = pd.DataFrame(tree_preds).T

    result = pd.DataFrame({
        "Run ID": runs_df["Run ID"],
        "File Name": runs_df["File Name"],
        "Predicted Score": pred_df.mean(axis=1),
        "Uncertainty": pred_df.std(axis=1),
    })

    return result.sort_values("Uncertainty", ascending=False)