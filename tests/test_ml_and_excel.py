from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from excel_utils import make_excel
from ml_engine import train_model


def test_ml_minimum_rows_and_target_leakage() -> None:
    small = pd.DataFrame(
        {"Cost Per Tonne": range(10), "feature > useful": range(10)}
    )
    model, _, _, metrics = train_model(small, "Cost Per Tonne")
    assert model is None
    assert "20" in metrics["error"]

    rng = np.random.default_rng(42)
    useful = np.arange(30, dtype=float)
    frame = pd.DataFrame(
        {
            "Run ID": range(30),
            "Cost Per Tonne": 400 - useful + rng.normal(0, 0.2, 30),
            "feature > useful": useful,
            "feature > total_cost_proxy": 400 - useful,
            "feature > sparse": [None if i % 3 == 0 else i for i in range(30)],
        }
    )
    model, features, importance, metrics = train_model(frame, "Cost Per Tonne")
    assert model is not None
    assert "feature > useful" in features
    assert "feature > total_cost_proxy" not in features
    assert set(("R2", "MAE", "RMSE")).issubset(metrics)
    assert not importance.empty


def test_excel_has_three_sheets_and_sanitizes_strings() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Sender": ["=HYPERLINK(\"bad\")"],
            "feature > event_count": [2],
        }
    )
    logs = pd.DataFrame({"run_id": [1], "event": ["Power\x00 on"]})
    output = make_excel(runs, logs)
    workbook = load_workbook(BytesIO(output.getvalue()), read_only=True)
    assert workbook.sheetnames == ["Runs", "Event Logs", "ML Features"]
    assert workbook["Runs"]["B2"].value.startswith("'")
    assert "\x00" not in workbook["Event Logs"]["B2"].value
