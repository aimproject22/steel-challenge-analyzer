"""Conservative Active Learning helpers for pipeline validation."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


CONTROLLABLE_TOKENS = (
    "raw_material",
    "added_kg",
    "total_carbon_kg",
    "total_lime_kg",
    "total_dolomite_kg",
    "total_iron_oxide_kg",
    "power_120_first_min",
    "power_75_first_min",
    "oxygen_150_first_min",
    "time_first_scrap",
    "time_second_scrap",
    "time_first_addition",
    "basket_interval",
    "power_start_to_second_basket",
)

OUTCOME_TOKENS = (
    "within_spec",
    "violation",
    "tapping_complete",
    "tapping_duration",
    "analysis_to_tapping",
)


def controllable_features(feature_cols: list[str]) -> list[str]:
    result = []
    for column in feature_cols:
        lowered = column.casefold().replace(" ", "_")
        if any(token in lowered for token in OUTCOME_TOKENS):
            continue
        if any(token in lowered for token in CONTROLLABLE_TOKENS):
            result.append(column)
    return result


def generate_candidates(
    model,
    runs_df: pd.DataFrame,
    feature_cols: list[str],
    *,
    candidate_count: int = 200,
    top_n: int = 20,
    random_state: int = 42,
) -> pd.DataFrame:
    """Generate candidates only inside observed controllable min/max bounds."""

    if model is None or runs_df.empty:
        return pd.DataFrame()
    controls = controllable_features(feature_cols)
    if not controls:
        return pd.DataFrame()

    numeric = runs_df.reindex(columns=feature_cols).apply(pd.to_numeric, errors="coerce")
    medians = pd.Series(getattr(model, "_steel_feature_medians", {})).reindex(feature_cols)
    medians = medians.fillna(numeric.median()).fillna(0.0)
    rng = np.random.default_rng(random_state)
    candidates = pd.DataFrame(
        np.tile(medians.to_numpy(), (candidate_count, 1)),
        columns=feature_cols,
    )

    valid_controls = []
    for column in controls:
        minimum = numeric[column].min()
        maximum = numeric[column].max()
        if pd.isna(minimum) or pd.isna(maximum) or minimum == maximum:
            continue
        candidates[column] = rng.uniform(float(minimum), float(maximum), candidate_count)
        valid_controls.append(column)
    if not valid_controls:
        return pd.DataFrame()

    tree_predictions = np.vstack(
        [tree.predict(candidates[feature_cols]) for tree in model.estimators_]
    ).T
    output = candidates[valid_controls].copy()
    output.insert(0, "Candidate", range(1, len(output) + 1))
    output["prediction_mean"] = tree_predictions.mean(axis=1)
    output["prediction_std"] = tree_predictions.std(axis=1)
    output["pipeline_validation_only"] = True
    return output.sort_values(
        "prediction_std", ascending=False, ignore_index=True
    ).head(top_n)

