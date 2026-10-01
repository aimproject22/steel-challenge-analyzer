from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from excel_utils import build_all_runs_export_df, make_excel
from export_features import extract_event_export_features
from ml_engine import get_safe_feature_columns, train_model


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


def _headers(worksheet) -> dict[str, int]:
    return {
        str(cell.value): index
        for index, cell in enumerate(worksheet[1], start=1)
        if cell.value is not None
    }


def test_excel_has_single_all_runs_sheet_and_sanitizes_strings() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Sender": ["=HYPERLINK(\"bad\")"],
            "feature > event_count": [2],
        }
    )
    logs = pd.DataFrame(
        {"run_id": [1], "log_no": [1], "time": ["00:00:00"], "event": ["Power\x00 on"]}
    )
    output = make_excel(runs, logs)
    workbook = load_workbook(BytesIO(output.getvalue()), read_only=False)
    assert workbook.sheetnames == ["ALL_RUNS"]
    worksheet = workbook["ALL_RUNS"]
    assert worksheet.freeze_panes == "G2"
    assert worksheet.auto_filter.ref == worksheet.dimensions
    headers = _headers(worksheet)
    assert worksheet.max_row == 2
    assert worksheet.cell(2, headers["META_sender"]).value.startswith("'")
    assert "\x00" not in worksheet.cell(2, headers["RAW_event_log_01"]).value
    assert worksheet.row_dimensions[2].height == 20
    assert not worksheet.cell(2, headers["RAW_event_log_01"]).alignment.wrap_text


def test_excel_two_runs_produce_two_rows_and_aggregate_logs() -> None:
    runs = pd.DataFrame({"Run ID": [1, 2], "Status": [1, 0]})
    logs = pd.DataFrame(
        {
            "run_id": [1, 2],
            "log_no": [1, 1],
            "time": ["00:00:00", "00:00:10"],
            "event": ["Power set to: 120 MW", "Tapping complete"],
        }
    )
    output = make_excel(runs, logs)
    workbook = load_workbook(BytesIO(output.getvalue()), read_only=True)
    worksheet = workbook["ALL_RUNS"]
    headers = _headers(worksheet)
    assert worksheet.max_row == 3
    assert "Power set to" in worksheet.cell(2, headers["RAW_event_log_01"]).value
    assert "Tapping complete" in worksheet.cell(3, headers["RAW_event_log_01"]).value


def test_dynamic_event_features_power_basket_addition_and_analysis() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Time": [30],
            "Total Energy kWh": [1000],
            "Raw Materials > No1 Bundles": [48],
            "Additions > Lime": [1430],
        }
    )
    event_rows = [
        ("00:00:00", "Power 120 MW"),
        ("00:00:32", "Scrap basket added with: No1 Bundles: 48t"),
        ("00:10:00", "Power 90 MW"),
        ("00:12:00", "Analysis Requested"),
        ("00:13:30", "Analysis received"),
        ("00:15:00", "Additions: Dolomite:930kg; Iron Oxide:700kg; Lime:1430kg"),
        ("00:20:00", "Power 0 MW"),
        ("00:25:00", "Power 180 MW"),
        ("00:30:00", "Tapping complete"),
    ]
    logs = pd.DataFrame(
        [
            {"run_id": 1, "log_no": index, "time": time, "event": event}
            for index, (time, event) in enumerate(event_rows, start=1)
        ]
    )
    frame = build_all_runs_export_df(runs, logs)
    row = frame.iloc[0]
    assert row["PWR_120MW_duration_sec"] == 600
    assert row["PWR_90MW_duration_sec"] == 600
    assert row["PWR_0MW_duration_sec"] == 300
    assert row["PWR_180MW_duration_sec"] == 300
    assert row["PWR_total_on_sec"] == 1500
    assert row["BASKET_1_mass_t"] == 48
    assert row["EVTADD_Dolomite_first_sec"] == 900
    assert row["EVTADD_Dolomite_total_kg"] == 930
    assert row["ANALYSIS_avg_wait_sec"] == 90
    assert row["RM_No1_Bundles_t"] == 48
    assert row["ADD_Lime_kg"] == 1430


def test_power_duration_reference_sequence() -> None:
    events = [
        {"time": "00:00:00", "event": "Power 120 MW"},
        {"time": "00:10:00", "event": "Power 90 MW"},
        {"time": "00:20:00", "event": "Power 0 MW"},
        {"time": "00:30:00", "event": "Tapping complete"},
    ]
    result = extract_event_export_features(events)
    assert result["PWR_120MW_duration_sec"] == 600
    assert result["PWR_90MW_duration_sec"] == 600
    assert result["PWR_0MW_duration_sec"] == 600
    assert result["PWR_total_on_sec"] == 1200


def test_oxygen_and_carbon_flow_integration() -> None:
    events = [
        {"time": "00:00:00", "event": "Oxygen flow changed: (100 Nm³ / min)"},
        {"time": "00:10:00", "event": "Oxygen flow changed: (150 Nm³ / min)"},
        {"time": "00:20:00", "event": "Oxygen flow changed: (0 Nm³ / min)"},
        {"time": "00:30:00", "event": "Carbon flow changed: 2 kg/min"},
        {"time": "00:40:00", "event": "Carbon flow changed: 0 kg/min"},
        {"time": "00:50:00", "event": "Tapping complete"},
    ]
    result = extract_event_export_features(events)
    assert result["O2_integrated_Nm3"] == 2500
    assert result["O2_total_on_sec"] == 1200
    assert result["CINJ_integrated_kg"] == 20
    assert result["CINJ_total_on_sec"] == 600


def test_raw_event_log_splits_without_data_loss() -> None:
    event = "X" * 65_000
    result = extract_event_export_features(
        [{"time": "00:00:00", "event": event}]
    )
    chunks = [
        result[key]
        for key in sorted(result)
        if key.startswith("RAW_event_log_")
    ]
    assert len(chunks) == 3
    assert "".join(chunks) == f"00:00:00,{event}"
    assert all(len(chunk) <= 30_000 for chunk in chunks)


def test_duplicate_parser_columns_are_collapsed_deterministically() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Additions > Lime": [100],
            "Additions > Lime_2": [100],
            "Additions > Lime_3": [200],
        }
    )
    frame = build_all_runs_export_df(runs, pd.DataFrame())
    assert frame.loc[0, "ADD_Lime_kg"] == 100
    assert not any(column.startswith("ADD_Lime_2") for column in frame.columns)
    assert frame.loc[0, "QUALITY_warning_count"] >= 2
    assert "Conflicting duplicate" in frame.loc[0, "QUALITY_warning_text"]


def test_excel_flags_a_database_row_containing_multiple_runs() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1035],
            "Run Information > Date": ["21/09/2026 10:00:00"],
            "Run Information > Date_2": ["21/09/2026 11:00:00"],
            "Run Information > Score": [410.0],
            "Run Information > Score_2": [405.0],
        }
    )

    frame = build_all_runs_export_df(runs, pd.DataFrame())
    assert frame.loc[0, "QUALITY_multi_run_merged_detected"] == 1
    assert "Multiple simulation runs" in frame.loc[0, "QUALITY_warning_text"]


def test_excel_flags_clock_reset_after_tapping_complete() -> None:
    runs = pd.DataFrame({"Run ID": [7]})
    logs = pd.DataFrame(
        {
            "run_id": [7, 7],
            "log_no": [1, 2],
            "time": ["00:40:00", "00:00:05"],
            "event": ["Tapping complete", "Simulation rate changed: 1"],
        }
    )
    frame = build_all_runs_export_df(runs, logs)
    assert frame.loc[0, "QUALITY_multi_run_merged_detected"] == 1


def test_all_null_and_spec_less_columns_are_not_exported() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Slag Composition > Al2O3 > Current": [4.8],
        }
    )
    frame = build_all_runs_export_df(runs, pd.DataFrame())
    assert "SLAG_Al2O3_pct" in frame
    assert "SLAG_Al2O3_min" not in frame
    assert "COST_power_corrected_usd" not in frame
    assert not any(frame[column].isna().all() for column in frame)


def test_duplicate_basket_materials_sum_and_reconstruct_report() -> None:
    runs = pd.DataFrame({"Run ID": [1]})
    logs = pd.DataFrame(
        {
            "run_id": [1, 1],
            "log_no": [1, 2],
            "time": ["00:00:00", "00:30:00"],
            "event": [
                "Scrap basket added with: Shredded: 22t; Direct Reduced Iron: 18t; Plate and Structural: 4t; Shredded: 18t",
                "Tapping complete",
            ],
        }
    )
    row = build_all_runs_export_df(runs, logs).iloc[0]
    assert row["BASKET_1_Shredded_t"] == 40
    assert row["BASKET_Shredded_total_t"] == 40
    assert row["RM_Shredded_t"] == 40
    assert row["QUALITY_rm_source"] == "EVENT_LOG_RECONSTRUCTED"


def test_additions_fallback_and_alias_normalization() -> None:
    runs = pd.DataFrame({"Run ID": [1]})
    logs = pd.DataFrame(
        {
            "run_id": [1, 1],
            "log_no": [1, 2],
            "time": ["00:10:00", "00:20:00"],
            "event": [
                "Additions: HC FeMn 600kg; Dolomite 930kg; IronOxide 700kg; Lime 1430kg",
                "Tapping complete",
            ],
        }
    )
    row = build_all_runs_export_df(runs, logs).iloc[0]
    assert row["ADD_High_C_Ferro_Manganese_kg"] == 600
    assert row["ADD_Dolomite_kg"] == 930
    assert row["ADD_Iron_Oxide_kg"] == 700
    assert row["ADD_Lime_kg"] == 1430
    assert row["QUALITY_add_source"] == "EVENT_LOG_RECONSTRUCTED"


def test_cost_mapping_detects_shift_without_price_hardcoding() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Time": [42],
            "Cost Breakdown > Power": [591.2],
            "Cost Breakdown > Scrap": [42],
            "Cost Breakdown > Additions": [16020],
            "Cost Breakdown > Other consumables": [19300.85],
            "Total Cost": [35912],
        }
    )
    row = build_all_runs_export_df(runs, pd.DataFrame()).iloc[0]
    assert row["COST_label_mismatch_detected"] == 1
    assert row["COST_power_corrected_usd"] == pytest.approx(19300.85)
    assert row["COST_scrap_corrected_usd"] == pytest.approx(16020)
    assert row["COST_additions_corrected_usd"] == pytest.approx(591.2)
    assert row["COST_other_corrected_usd"] == pytest.approx(0)
    assert row["COST_reconciliation_error_usd"] == pytest.approx(0.05)


def test_chemistry_pass_is_not_mislabeled_as_overall_pass() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Status": [0],
            "Steel Composition > C > Current": [0.11],
            "Steel Composition > C > Min": [0.10],
            "Steel Composition > C > Max": [0.12],
        }
    )
    frame = build_all_runs_export_df(runs, pd.DataFrame())
    assert frame.loc[0, "QUALITY_chemistry_pass"] == 1
    assert frame.loc[0, "QUALITY_full_validation_available"] == 0
    assert "QUALITY_overall_pass" not in frame.columns


def test_power_integral_names_do_not_claim_actual_energy() -> None:
    result = extract_event_export_features(
        [
            {"time": "00:00:00", "event": "Power 120 MW"},
            {"time": "00:10:00", "event": "Tapping complete"},
        ]
    )
    assert result["PWR_setpoint_integral_MW_sec"] == 72_000
    assert result["PWR_setpoint_equivalent_MWh"] == 20
    assert "PWR_energy_estimated_kwh" not in result


def test_ml_roles_exclude_post_run_quality_features() -> None:
    frame = pd.DataFrame(
        {
            "feature > basket_total": [1],
            "feature > steel_c_within_spec": [1],
            "feature > raw_event_sequence": [1],
            "feature > total_cost_proxy": [1],
        }
    )
    assert get_safe_feature_columns(frame, "Cost Per Tonne") == [
        "feature > basket_total"
    ]


def test_excel_collapses_detail_columns_but_keeps_core_visible() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1],
            "Steel Composition > C > Current": [0.11],
            "Steel Composition > C > Min": [0.10],
            "Steel Composition > C > Max": [0.12],
        }
    )
    logs = pd.DataFrame(
        {
            "run_id": [1],
            "log_no": [1],
            "time": ["00:10:00"],
            "event": ["Tapping complete"],
        }
    )
    workbook = load_workbook(BytesIO(make_excel(runs, logs).getvalue()))
    worksheet = workbook["ALL_RUNS"]
    headers = _headers(worksheet)
    current_letter = worksheet.cell(1, headers["STEEL_C_wt_pct"]).column_letter
    detail_letter = worksheet.cell(1, headers["STEEL_C_min"]).column_letter
    raw_letter = worksheet.cell(1, headers["RAW_event_log_01"]).column_letter
    assert not worksheet.column_dimensions[current_letter].hidden
    assert worksheet.column_dimensions[detail_letter].hidden
    assert worksheet.column_dimensions[raw_letter].hidden


def test_reference_success_run_is_preserved() -> None:
    runs = pd.DataFrame(
        {
            "Run ID": [1029],
            "Status": [1],
            "Score": [406.62],
            "Time": [42],
            "Tapping Mass": [88],
            "Tap Temperature": [1634],
            "Total Energy kWh": [33861],
            "Energy kWh/t": [383],
            "Cost Per Tonne": [406.62],
            "Raw Materials > No1 Bundles": [89],
            "Additions > High C Ferro-Manganese": [600],
            "Additions > Dolomite": [930],
            "Additions > Iron Oxide": [700],
            "Additions > Lime": [1430],
            "Steel Composition > C > Current": [0.115],
            "Steel Composition > C > Min": [0.100],
            "Steel Composition > C > Max": [0.120],
            "Slag Composition > CaO > Current": [43.934],
            "Slag Composition > SiO2 > Current": [25.750],
            "Slag Composition > Basicity > Current": [1.70620239624785],
            "Slag Composition > Basicity > Min": [1.5],
            "Slag Composition > Basicity > Max": [2.5],
        }
    )
    events = [
        ("00:00:32", "Scrap basket added with: No1 Bundles: 48t"),
        ("00:01:35", "Power set to: 200 MW"),
        ("00:01:58", "Power set to: 120 MW"),
        ("00:18:00", "Scrap basket added with: No1 Bundles: 41t"),
        ("00:31:16", "Power set to: 105 MW"),
        ("00:32:15", "Additions: High C Ferro-Manganese: 600kg"),
        ("00:32:51", "Additions: Dolomite: 930kg; Iron Oxide: 700kg; Lime: 1430kg"),
        ("00:40:58", "Tapping start"),
        ("00:42:51", "Tapping complete"),
    ]
    logs = pd.DataFrame(
        [
            {"run_id": 1029, "log_no": index, "time": time, "event": event}
            for index, (time, event) in enumerate(events, start=1)
        ]
    )
    row = build_all_runs_export_df(runs, logs).iloc[0]
    assert row["COST_per_tonne_usd"] == pytest.approx(406.62)
    assert row["PERF_time_min"] == 42
    assert row["PERF_tapping_mass_t"] == 88
    assert row["PERF_tap_temperature_c"] == 1634
    assert row["PERF_total_energy_kwh"] == 33861
    assert row["RM_No1_Bundles_t"] == 89
    assert row["BASKET_No1_Bundles_total_t"] == 89
    assert row["ADD_High_C_Ferro_Manganese_kg"] == 600
    assert row["ADD_Lime_kg"] == 1430
    assert row["STEEL_C_wt_pct"] == pytest.approx(0.115)
    assert row["SLAG_Basicity"] == pytest.approx(1.70620239624785)
    assert row["QUALITY_rm_source"] == "REPORT_AND_LOG_VERIFIED"
    assert row["QUALITY_add_source"] == "REPORT_AND_LOG_VERIFIED"
