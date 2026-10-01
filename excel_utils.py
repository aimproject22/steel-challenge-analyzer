"""Permission-neutral, single-sheet Excel generation helpers."""

from __future__ import annotations

import math
import re
from io import BytesIO
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from canonical import normalize_material_name
from export_features import export_token, extract_event_export_features


ILLEGAL_EXCEL_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")
DUPLICATE_SUFFIX_RE = re.compile(r"_(\d+)$")
COMPOSITE_RUN_KEY_RE = re.compile(
    r"^Run Information > (?:User Id|Date|Status|Score)_\d+$"
)

CORE_META_COLUMNS = [
    "META_run_id", "META_run_date", "META_uploaded_at", "META_source",
    "META_source_run_index",
    "META_sender", "META_sender_email", "META_steel_user_id",
    "META_process_type", "META_uploader", "META_file_name", "META_status",
    "META_score", "META_user_level", "META_steel_grade",
]
CORE_PERF_COLUMNS = [
    "PERF_time_min", "PERF_time_sec", "PERF_tapping_mass_t",
    "PERF_tap_temperature_c", "PERF_total_energy_kwh",
    "PERF_energy_kwh_per_t_reported", "PERF_energy_kwh_per_t_calc",
    "PERF_energy_kwh_per_t_error", "PERF_score",
]
CORE_COST_COLUMNS = [
    "COST_power_raw", "COST_scrap_raw", "COST_additions_raw",
    "COST_other_consumables_raw", "COST_total_usd", "COST_per_tonne_usd",
    "COST_power_corrected_usd", "COST_scrap_corrected_usd",
    "COST_additions_corrected_usd", "COST_other_corrected_usd",
    "COST_reconstructed_total_usd",
    "COST_reconciliation_error_usd", "COST_reconciliation_error_pct",
    "COST_detected_power_source", "COST_detected_scrap_source",
    "COST_detected_additions_source", "COST_detected_other_source",
    "COST_label_mismatch_detected", "COST_mapping_confidence",
    "COST_mapping_warning",
]
CORE_QUALITY_COLUMNS = [
    "QUALITY_overall_pass", "QUALITY_full_validation_available",
    "QUALITY_chemistry_pass", "QUALITY_steel_pass",
    "QUALITY_steel_violation_count", "QUALITY_slag_pass",
    "QUALITY_slag_violation_count", "QUALITY_temperature_pass",
    "QUALITY_time_pass", "QUALITY_mass_pass", "QUALITY_co2_pass",
    "QUALITY_status_consistent", "QUALITY_status_matches_chemistry",
    "QUALITY_multi_run_merged_detected",
    "QUALITY_rm_source", "QUALITY_rm_report_log_match",
    "QUALITY_rm_reconstruction_warning", "QUALITY_rm_total_report_t",
    "QUALITY_rm_total_log_t", "QUALITY_rm_total_difference_t",
    "QUALITY_add_source", "QUALITY_add_report_log_match",
    "QUALITY_add_reconstruction_warning", "QUALITY_add_total_report_kg",
    "QUALITY_add_total_log_kg", "QUALITY_add_total_difference_kg",
    "QUALITY_unknown_event_count", "QUALITY_warning_count",
    "QUALITY_warning_text",
]
GROUP_PREFIXES = (
    "RM_", "ADD_", "STEEL_", "SLAG_", "BASKET_", "PWR_", "O2_",
    "CINJ_", "EVTADD_", "ANALYSIS_", "TAP_", "DERIVED_", "RAW_",
)


def remove_illegal_characters(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = ILLEGAL_EXCEL_RE.sub("", value)
    if cleaned.startswith(("=", "+", "-", "@")):
        cleaned = "'" + cleaned
    return cleaned


def clean_dataframe_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    for column in cleaned.select_dtypes(include=["object", "string"]).columns:
        cleaned[column] = cleaned[column].map(remove_illegal_characters)
    return cleaned


def feature_frame(runs_df: pd.DataFrame) -> pd.DataFrame:
    """Retain the existing ML-facing helper for backward compatibility."""

    feature_columns = [
        column for column in runs_df.columns if str(column).startswith("feature >")
    ]
    identity_columns = [
        column
        for column in (
            "Run ID", "Run Date", "Sender", "Sender Email", "Steel User ID",
            "Steel Grade", "Score", "Cost Per Tonne", "Time",
        )
        if column in runs_df.columns
    ]
    return runs_df[identity_columns + feature_columns].copy()


def normalize_export_name(value: Any) -> str:
    return export_token(value)


def _is_missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, (dict, list, tuple, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if not _is_missing(value):
            return value
    return None


def _number(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _run_key(value: Any) -> str:
    number = _number(value)
    if number is not None and number.is_integer():
        return str(int(number))
    return str(value or "")


def aggregate_logs_by_run(logs_df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Group logs once so export remains O(runs + logs)."""

    if logs_df is None or logs_df.empty or "run_id" not in logs_df.columns:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for run_id, group in logs_df.groupby("run_id", sort=False, dropna=False):
        result[_run_key(run_id)] = group.to_dict("records")
    return result


def _section_values(
    row: Mapping[str, Any], section: str
) -> tuple[dict[str, Any], list[str]]:
    """Collapse parser duplicate suffixes without trailing Excel columns."""

    prefix = f"{section} > "
    values: dict[str, Any] = {}
    warnings: list[str] = []
    for raw_key, value in row.items():
        key = str(raw_key)
        if not key.startswith(prefix) or " > " in key[len(prefix) :]:
            continue
        if isinstance(value, (dict, list)) or _is_missing(value):
            continue
        raw_name = key[len(prefix) :]
        name = DUPLICATE_SUFFIX_RE.sub("", raw_name)
        if name not in values:
            values[name] = value
        elif str(values[name]) != str(value):
            warnings.append(
                f"Conflicting duplicate {section} value for {name}: "
                f"kept {values[name]!r}, ignored {value!r}"
            )
    return values, warnings


def _normalize_section_materials(
    values: Mapping[str, Any], *, addition: bool
) -> tuple[dict[str, Any], list[str]]:
    normalized: dict[str, Any] = {}
    warnings: list[str] = []
    for raw_name, value in values.items():
        name = normalize_material_name(raw_name, addition=addition)
        if name not in normalized:
            normalized[name] = value
            continue
        previous = _number(normalized[name])
        current = _number(value)
        if previous is not None and current is not None:
            normalized[name] = previous + current
            warnings.append(f"Summed duplicate material {name}")
        elif str(normalized[name]) != str(value):
            warnings.append(
                f"Conflicting duplicate material {name}: kept {normalized[name]!r}"
            )
    return normalized, warnings


def build_material_columns(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    values, warnings = _section_values(row, "Raw Materials")
    values, alias_warnings = _normalize_section_materials(values, addition=False)
    warnings.extend(alias_warnings)
    result: dict[str, Any] = {}
    numeric: list[float] = []
    nonzero = 0
    for name, value in values.items():
        number = _number(value)
        result[f"RM_{normalize_export_name(name)}_t"] = number if number is not None else value
        if number is not None:
            numeric.append(number)
            nonzero += int(number != 0)
    result["RM_total_mass_t"] = sum(numeric) if numeric else None
    result["RM_nonzero_material_count"] = nonzero if values else None
    return result, warnings


def build_addition_columns(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    values, warnings = _section_values(row, "Additions")
    values, alias_warnings = _normalize_section_materials(values, addition=True)
    warnings.extend(alias_warnings)
    result: dict[str, Any] = {}
    numeric: list[float] = []
    nonzero = 0
    for name, value in values.items():
        number = _number(value)
        result[f"ADD_{normalize_export_name(name)}_kg"] = number if number is not None else value
        if number is not None:
            numeric.append(number)
            nonzero += int(number != 0)
    result["ADD_total_kg"] = sum(numeric) if numeric else None
    result["ADD_nonzero_count"] = nonzero if values else None
    return result, warnings


def _composition_source(
    row: Mapping[str, Any], section: str, nested_key: str
) -> dict[str, dict[str, Any]]:
    prefix = f"{section} > "
    result: dict[str, dict[str, Any]] = {}
    for raw_key, value in row.items():
        key = str(raw_key)
        if not key.startswith(prefix) or isinstance(value, (dict, list)):
            continue
        parts = key[len(prefix) :].split(" > ")
        element = parts[0]
        field = parts[-1].casefold() if len(parts) > 1 else "current"
        if field in {"current", "min", "max"} and not _is_missing(value):
            result.setdefault(element, {})[field] = value

    nested = row.get(nested_key)
    if isinstance(nested, Mapping):
        for element, values in nested.items():
            if not isinstance(values, Mapping):
                continue
            target = result.setdefault(str(element), {})
            for field in ("current", "min", "max"):
                if field not in target and not _is_missing(values.get(field)):
                    target[field] = values.get(field)
    return result


def _composition_columns(
    row: Mapping[str, Any], section: str, prefix: str, nested_key: str
) -> tuple[dict[str, Any], Optional[int], int]:
    source = _composition_source(row, section, nested_key)
    result: dict[str, Any] = {}
    pass_values: list[int] = []
    violations = 0
    for element, values in source.items():
        token = normalize_export_name(element)
        current = _number(values.get("current"))
        minimum = _number(values.get("min"))
        maximum = _number(values.get("max"))
        current_suffix = (
            "Basicity"
            if prefix == "SLAG_" and token.casefold() == "basicity"
            else (f"{token}_wt_pct" if prefix == "STEEL_" else f"{token}_pct")
        )
        result[f"{prefix}{current_suffix}"] = current
        has_spec = minimum is not None or maximum is not None
        if has_spec:
            result[f"{prefix}{token}_min"] = minimum
            result[f"{prefix}{token}_max"] = maximum
        if current is None or (minimum is None and maximum is None):
            within = None
        else:
            within = int(
                (minimum is None or current >= minimum)
                and (maximum is None or current <= maximum)
            )
            pass_values.append(within)
            violations += int(not within)
        if has_spec:
            result[f"{prefix}{token}_pass"] = within
            if minimum is not None:
                result[f"{prefix}{token}_margin_low"] = (
                    current - minimum if current is not None else None
                )
            if maximum is not None:
                result[f"{prefix}{token}_margin_high"] = (
                    maximum - current if current is not None else None
                )
            if minimum is not None and maximum is not None:
                result[f"{prefix}{token}_normalized_position"] = (
                    (current - minimum) / (maximum - minimum)
                    if current is not None and maximum != minimum else None
                )
    overall = int(all(pass_values)) if pass_values else None
    return result, overall, violations


def build_steel_columns(row: Mapping[str, Any]) -> tuple[dict[str, Any], Optional[int], int]:
    return _composition_columns(row, "Steel Composition", "STEEL_", "steel_composition")


def build_slag_columns(row: Mapping[str, Any]) -> tuple[dict[str, Any], Optional[int], int]:
    result, overall, violations = _composition_columns(
        row, "Slag Composition", "SLAG_", "slag_composition"
    )
    cao = _number(result.get("SLAG_CaO_pct"))
    sio2 = _number(result.get("SLAG_SiO2_pct"))
    reported = _number(result.get("SLAG_Basicity"))
    calculated = cao / sio2 if cao is not None and sio2 not in (None, 0) else None
    result["SLAG_Basicity_calc"] = calculated
    result["SLAG_Basicity_error"] = (
        reported - calculated if reported is not None and calculated is not None else None
    )
    return result, overall, violations


def build_event_export_features(
    logs: Iterable[Mapping[str, Any]], row: Mapping[str, Any]
) -> dict[str, Any]:
    return extract_event_export_features(
        logs,
        reported_time_minutes=_value(row, "Time", "Cost Breakdown > Time (in minutes)"),
        reported_energy_kwh=_value(row, "Total Energy kWh", "Cost Breakdown > Total Energy"),
    )


def _base_export_row(row: Mapping[str, Any]) -> dict[str, Any]:
    process_time = _number(_value(row, "Time", "Cost Breakdown > Time (in minutes)"))
    tapping_mass = _number(_value(row, "Tapping Mass", "Cost Breakdown > Tapping mass"))
    total_energy = _number(_value(row, "Total Energy kWh", "Cost Breakdown > Total Energy"))
    reported_energy_per_t = _number(
        _value(row, "Energy kWh/t", "Cost Breakdown > Total Energy_2")
    )
    calculated_energy_per_t = (
        total_energy / tapping_mass
        if total_energy is not None and tapping_mass not in (None, 0) else None
    )
    total_cost = _number(_value(row, "Total Cost", "Cost Breakdown > Total Cost"))
    cost_per_tonne = _number(_value(row, "Cost Per Tonne", "Cost Breakdown > Cost Per Tonne"))
    result = {
        "META_run_id": _value(row, "Run ID", "id"),
        "META_run_date": _value(row, "Run Date", "Run Information > Date"),
        "META_uploaded_at": _value(row, "Uploaded At", "uploaded_at", "created_at"),
        "META_source": _value(row, "Source", "source"),
        "META_source_run_index": _number(
            _value(row, "Source Run Index", "source_run_index")
        ),
        "META_sender": _value(row, "Sender", "sender_name"),
        "META_sender_email": _value(row, "Sender Email", "sender_email"),
        "META_steel_user_id": _value(row, "Steel User ID", "Run Information > User Id"),
        "META_process_type": _value(row, "Process Type", "Run Information > Process Type"),
        "META_uploader": _value(row, "Uploader", "uploader"),
        "META_file_name": _value(row, "File Name", "file_name"),
        "META_status": _number(_value(row, "Status", "Run Information > Status")),
        "META_score": _number(_value(row, "Score", "Run Information > Score")),
        "META_user_level": _value(row, "User Level", "Simulation Settings > User Level"),
        "META_steel_grade": _value(row, "Steel Grade", "Simulation Settings > Steel Grade"),
        "PERF_time_min": process_time,
        "PERF_time_sec": process_time * 60.0 if process_time is not None else None,
        "PERF_tapping_mass_t": tapping_mass,
        "PERF_tap_temperature_c": _number(_value(row, "Tap Temperature", "Cost Breakdown > Tap temperature")),
        "PERF_total_energy_kwh": total_energy,
        "PERF_energy_kwh_per_t_reported": reported_energy_per_t,
        "PERF_energy_kwh_per_t_calc": calculated_energy_per_t,
        "PERF_energy_kwh_per_t_error": (
            reported_energy_per_t - calculated_energy_per_t
            if reported_energy_per_t is not None and calculated_energy_per_t is not None else None
        ),
        "PERF_score": _number(_value(row, "Score", "Run Information > Score")),
        "COST_power_raw": _number(_value(row, "Cost Breakdown > Power")),
        "COST_scrap_raw": _number(_value(row, "Cost Breakdown > Scrap")),
        "COST_additions_raw": _number(_value(row, "Cost Breakdown > Additions")),
        "COST_other_consumables_raw": _number(_value(row, "Cost Breakdown > Other consumables")),
        "COST_total_usd": total_cost,
        "COST_per_tonne_usd": cost_per_tonne,
        "COST_power_corrected_usd": None,
        "COST_scrap_corrected_usd": None,
        "COST_additions_corrected_usd": None,
        "COST_other_corrected_usd": None,
        "COST_reconstructed_total_usd": None,
        "COST_reconciliation_error_usd": None,
        "COST_reconciliation_error_pct": None,
    }
    result["DERIVED_energy_per_process_min"] = (
        total_energy / process_time if total_energy is not None and process_time not in (None, 0) else None
    )
    result["DERIVED_cost_per_process_min"] = (
        total_cost / process_time if total_cost is not None and process_time not in (None, 0) else None
    )
    result["DERIVED_cost_per_tapping_t"] = (
        total_cost / tapping_mass if total_cost is not None and tapping_mass not in (None, 0) else None
    )
    return result


def _nearly_equal(left: Any, right: Any, *, absolute: float = 0.01) -> bool:
    first = _number(left)
    second = _number(right)
    if first is None or second is None:
        return False
    return abs(first - second) <= max(absolute, 1e-4 * max(abs(first), abs(second)))


def apply_cost_validation(export_row: dict[str, Any]) -> list[str]:
    """Validate report labels without inventing material or electricity prices."""

    power = _number(export_row.get("COST_power_raw"))
    scrap = _number(export_row.get("COST_scrap_raw"))
    additions = _number(export_row.get("COST_additions_raw"))
    other = _number(export_row.get("COST_other_consumables_raw"))
    total = _number(export_row.get("COST_total_usd"))
    process_time = _number(export_row.get("PERF_time_min"))
    warnings: list[str] = []
    if all(value is None for value in (power, scrap, additions, other, total)):
        export_row.update(
            {
                "COST_label_mismatch_detected": None,
                "COST_mapping_confidence": "MISSING",
                "COST_mapping_warning": None,
            }
        )
        return warnings
    time_collision = scrap is not None and _nearly_equal(scrap, process_time)
    meaningful = [value for value in (power, additions, other) if value is not None]
    residual = total - sum(meaningful) if total is not None and len(meaningful) == 3 else None

    if (
        time_collision
        and len(meaningful) == 3
        and total is not None
        and residual is not None
        and residual >= -max(1.0, abs(total) * 0.001)
        and residual <= max(1_000.0, abs(total) * 0.10)
    ):
        if _nearly_equal(residual, 0.0, absolute=0.1):
            residual = 0.0
        corrected = {
            "COST_power_corrected_usd": other,
            "COST_scrap_corrected_usd": additions,
            "COST_additions_corrected_usd": power,
            "COST_other_corrected_usd": residual,
            "COST_detected_power_source": "Cost Breakdown > Other consumables",
            "COST_detected_scrap_source": "Cost Breakdown > Additions",
            "COST_detected_additions_source": "Cost Breakdown > Power",
            "COST_detected_other_source": "TOTAL_RESIDUAL",
            "COST_label_mismatch_detected": 1,
            "COST_mapping_confidence": (
                "HIGH_INTERNAL_RECONCILIATION"
                if abs(residual) <= max(1.0, abs(total) * 0.001)
                else "MEDIUM_TOTAL_RESIDUAL"
            ),
            "COST_mapping_warning": (
                "Report cost labels appear shifted; Scrap raw equals process time. "
                "Corrected mapping uses the report total residual and no invented prices."
            ),
        }
        warnings.append("Cost label mismatch detected and internally reconciled")
    else:
        all_raw = [value for value in (power, scrap, additions, other) if value is not None]
        if (
            len(all_raw) == 4
            and total is not None
            and _nearly_equal(sum(all_raw), total, absolute=0.1)
        ):
            corrected = {
                "COST_power_corrected_usd": power,
                "COST_scrap_corrected_usd": scrap,
                "COST_additions_corrected_usd": additions,
                "COST_other_corrected_usd": other,
                "COST_detected_power_source": "Cost Breakdown > Power",
                "COST_detected_scrap_source": "Cost Breakdown > Scrap",
                "COST_detected_additions_source": "Cost Breakdown > Additions",
                "COST_detected_other_source": "Cost Breakdown > Other consumables",
                "COST_label_mismatch_detected": 0,
                "COST_mapping_confidence": "REPORT_TOTAL_RECONCILED",
                "COST_mapping_warning": None,
            }
        else:
            corrected = {
                "COST_label_mismatch_detected": int(time_collision),
                "COST_mapping_confidence": "UNRESOLVED",
                "COST_mapping_warning": (
                    "Scrap raw equals process time; corrected costs left blank"
                    if time_collision
                    else "Cost labels could not be independently validated"
                ),
            }
            warnings.append(str(corrected["COST_mapping_warning"]))

    export_row.update(corrected)
    corrected_values = [
        _number(export_row.get(column))
        for column in (
            "COST_power_corrected_usd",
            "COST_scrap_corrected_usd",
            "COST_additions_corrected_usd",
            "COST_other_corrected_usd",
        )
    ]
    if all(value is not None for value in corrected_values):
        reconstructed = sum(value for value in corrected_values if value is not None)
        export_row["COST_reconstructed_total_usd"] = reconstructed
        if total is not None:
            error = reconstructed - total
            export_row["COST_reconciliation_error_usd"] = error
            export_row["COST_reconciliation_error_pct"] = (
                error / total * 100.0 if total != 0 else None
            )
    return warnings


def _detail_values(
    values: Mapping[str, Any], prefix: str, suffix: str, excluded: set[str]
) -> dict[str, float]:
    result: dict[str, float] = {}
    for column, value in values.items():
        if not str(column).startswith(prefix) or not str(column).endswith(suffix):
            continue
        if column in excluded:
            continue
        number = _number(value)
        if number is not None:
            token = str(column)[len(prefix) : -len(suffix)]
            result[token] = result.get(token, 0.0) + number
    return result


def _material_match(report: Mapping[str, float], logged: Mapping[str, float]) -> bool:
    if not report or not logged:
        return False
    for name in set(report) | set(logged):
        if not _nearly_equal(report.get(name, 0.0), logged.get(name, 0.0), absolute=0.01):
            return False
    return True


def apply_material_fallbacks(export_row: dict[str, Any]) -> list[str]:
    """Prefer report tables, otherwise reconstruct material totals from events."""

    warnings: list[str] = []
    report_rm = _detail_values(
        export_row,
        "RM_",
        "_t",
        {"RM_total_mass_t"},
    )
    logged_rm = _detail_values(export_row, "BASKET_", "_total_t", set())
    report_add = _detail_values(
        export_row,
        "ADD_",
        "_kg",
        {"ADD_total_kg"},
    )
    logged_add = _detail_values(export_row, "EVTADD_", "_total_kg", set())

    rm_report_total = sum(report_rm.values()) if report_rm else None
    rm_log_total = sum(logged_rm.values()) if logged_rm else None
    add_report_total = sum(report_add.values()) if report_add else None
    add_log_total = sum(logged_add.values()) if logged_add else None
    export_row.update(
        {
            "QUALITY_rm_total_report_t": rm_report_total,
            "QUALITY_rm_total_log_t": rm_log_total,
            "QUALITY_rm_total_difference_t": (
                rm_report_total - rm_log_total
                if rm_report_total is not None and rm_log_total is not None else None
            ),
            "QUALITY_add_total_report_kg": add_report_total,
            "QUALITY_add_total_log_kg": add_log_total,
            "QUALITY_add_total_difference_kg": (
                add_report_total - add_log_total
                if add_report_total is not None and add_log_total is not None else None
            ),
        }
    )

    if report_rm:
        match = _material_match(report_rm, logged_rm) if logged_rm else None
        export_row["QUALITY_rm_source"] = (
            "REPORT_AND_LOG_VERIFIED" if match else "REPORT"
        )
        export_row["QUALITY_rm_report_log_match"] = int(match) if match is not None else None
        if match is False:
            export_row["QUALITY_rm_reconstruction_warning"] = "Report and Event Log RM differ"
            warnings.append("Raw Materials report/log mismatch")
    elif logged_rm:
        for token, value in logged_rm.items():
            export_row[f"RM_{token}_t"] = value
        export_row["RM_total_mass_t"] = rm_log_total
        export_row["RM_nonzero_material_count"] = sum(value != 0 for value in logged_rm.values())
        export_row["QUALITY_rm_source"] = "EVENT_LOG_RECONSTRUCTED"
        export_row["QUALITY_rm_reconstruction_warning"] = "Report RM missing; reconstructed from Event Log"
    else:
        export_row["QUALITY_rm_source"] = "MISSING"
        export_row["QUALITY_rm_reconstruction_warning"] = "Raw Materials unavailable"
        warnings.append("Raw Materials missing")

    if report_add:
        match = _material_match(report_add, logged_add) if logged_add else None
        export_row["QUALITY_add_source"] = (
            "REPORT_AND_LOG_VERIFIED" if match else "REPORT"
        )
        export_row["QUALITY_add_report_log_match"] = int(match) if match is not None else None
        if match is False:
            export_row["QUALITY_add_reconstruction_warning"] = "Report and Event Log additions differ"
            warnings.append("Additions report/log mismatch")
    elif logged_add:
        for token, value in logged_add.items():
            export_row[f"ADD_{token}_kg"] = value
        export_row["ADD_total_kg"] = add_log_total
        export_row["ADD_nonzero_count"] = sum(value != 0 for value in logged_add.values())
        export_row["QUALITY_add_source"] = "EVENT_LOG_RECONSTRUCTED"
        export_row["QUALITY_add_reconstruction_warning"] = "Report additions missing; reconstructed from Event Log"
    else:
        export_row["QUALITY_add_source"] = "MISSING"
        export_row["QUALITY_add_reconstruction_warning"] = "Additions unavailable"
    return warnings


def detect_multi_run_merged(row: Mapping[str, Any], logs: Iterable[Mapping[str, Any]]) -> bool:
    duplicate_fields = any(
        COMPOSITE_RUN_KEY_RE.match(str(key)) and not _is_missing(value)
        for key, value in row.items()
    )
    ordered = sorted(
        list(logs),
        key=lambda item: int(_number(item.get("log_no")) or 0),
    )
    events = [str(item.get("event") or "").casefold() for item in ordered]
    tapping_seen = False
    clock_reset = False
    tapping_seconds: Optional[float] = None
    for item, event in zip(ordered, events):
        seconds = _number(item.get("event_seconds"))
        if seconds is None:
            time_text = str(item.get("event_time") or item.get("time") or "")
            parts = time_text.split(":")
            try:
                seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            except (IndexError, TypeError, ValueError):
                seconds = None
        if tapping_seen and seconds is not None and tapping_seconds is not None:
            if seconds < tapping_seconds:
                clock_reset = True
        if "tapping complete" in event:
            tapping_seen = True
            tapping_seconds = seconds
    return bool(
        duplicate_fields
        or sum("tapping complete" in event for event in events) > 1
        or sum("selected steel grade" in event for event in events) > 1
        or clock_reset
    )


def drop_all_null_export_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if frame.empty:
        return frame, []
    null_columns = [
        column
        for column in frame.columns
        if frame[column].map(_is_missing).all()
    ]
    return frame.drop(columns=null_columns), null_columns


def drop_redundant_export_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop only known presentation duplicates that are identical for all runs."""

    pairs = (
        ("PERF_score", "META_score"),
        ("PWR_energy_reported_kwh", "PERF_total_energy_kwh"),
        ("META_uploader", "META_sender"),
    )
    dropped: list[str] = []
    for candidate, retained in pairs:
        if candidate in frame and retained in frame and frame[candidate].equals(frame[retained]):
            dropped.append(candidate)
    return frame.drop(columns=dropped), dropped


def _column_order(columns: Iterable[str]) -> list[str]:
    available = set(columns)
    ordered: list[str] = []

    def natural_key(value: str) -> list[tuple[int, Any]]:
        return [
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", value)
            if part
        ]

    for column in CORE_META_COLUMNS + CORE_PERF_COLUMNS + CORE_COST_COLUMNS + CORE_QUALITY_COLUMNS:
        if column in available:
            ordered.append(column)
    for prefix in GROUP_PREFIXES:
        group = [column for column in available if column.startswith(prefix)]
        if prefix == "RAW_":
            preferred = ["RAW_event_sequence", "RAW_unknown_events"]
            ordered.extend(column for column in preferred if column in group)
            group = [column for column in group if column not in preferred]
        ordered.extend(sorted(group, key=natural_key))
    ordered.extend(sorted(available - set(ordered), key=lambda value: value.casefold()))
    return ordered


def build_all_runs_export_df(
    runs_df: pd.DataFrame,
    logs_df: pd.DataFrame,
    ml_features_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Return the deterministic 1-run-per-row ALL_RUNS export frame."""

    del ml_features_df
    log_groups = aggregate_logs_by_run(logs_df)
    records: list[dict[str, Any]] = []
    source_runs = runs_df if runs_df is not None else pd.DataFrame()
    source_records = source_runs.to_dict("records")
    for row in source_records:
        export_row = _base_export_row(row)
        warnings: list[str] = []
        warnings.extend(apply_cost_validation(export_row))
        materials, material_warnings = build_material_columns(row)
        additions, addition_warnings = build_addition_columns(row)
        steel, steel_pass, steel_violations = build_steel_columns(row)
        slag, slag_pass, slag_violations = build_slag_columns(row)
        warnings.extend(material_warnings)
        warnings.extend(addition_warnings)
        export_row.update(materials)
        export_row.update(additions)
        export_row.update(steel)
        export_row.update(slag)

        run_logs = log_groups.get(_run_key(export_row.get("META_run_id")), [])
        event_features = build_event_export_features(run_logs, row)
        warnings.extend(event_features.pop("_warnings", []))
        export_row.update(event_features)
        warnings.extend(apply_material_fallbacks(export_row))
        merged = int(detect_multi_run_merged(row, run_logs))
        if merged:
            warnings.append("Multiple simulation runs may be merged in this DB record")
        export_row.update({
            "QUALITY_steel_pass": steel_pass,
            "QUALITY_steel_violation_count": steel_violations,
            "QUALITY_slag_pass": slag_pass,
            "QUALITY_slag_violation_count": slag_violations,
            "QUALITY_temperature_pass": None,
            "QUALITY_time_pass": None,
            "QUALITY_mass_pass": None,
            "QUALITY_co2_pass": None,
            "QUALITY_full_validation_available": 0,
            "QUALITY_multi_run_merged_detected": merged,
        })
        known_quality = [value for value in (steel_pass, slag_pass) if value is not None]
        chemistry = int(all(known_quality)) if known_quality else None
        export_row["QUALITY_chemistry_pass"] = chemistry
        export_row["QUALITY_overall_pass"] = None
        status = _number(export_row.get("META_status"))
        export_row["QUALITY_status_consistent"] = None
        export_row["QUALITY_status_matches_chemistry"] = (
            int(status == chemistry)
            if status is not None and chemistry is not None else None
        )
        if not run_logs:
            warnings.append("Event Log missing")
        export_row["QUALITY_warning_count"] = len(warnings)
        export_row["QUALITY_warning_text"] = " | ".join(warnings)
        records.append(export_row)

    frame = pd.DataFrame(records)
    if frame.empty:
        frame = pd.DataFrame(
            columns=CORE_META_COLUMNS + CORE_PERF_COLUMNS + CORE_COST_COLUMNS + CORE_QUALITY_COLUMNS
        )
        return frame
    frame, _ = drop_all_null_export_columns(frame)
    frame, _ = drop_redundant_export_columns(frame)
    return frame.reindex(columns=_column_order(frame.columns))


def apply_excel_formatting(writer: pd.ExcelWriter, frame: pd.DataFrame) -> None:
    worksheet = writer.book["ALL_RUNS"]
    worksheet.freeze_panes = "G2"
    worksheet.auto_filter.ref = worksheet.dimensions
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.zoomScale = 85
    worksheet.sheet_properties.outlinePr.summaryRight = True
    group_colors = {
        "META_": "1F4E78", "PERF_": "0F6B5D", "COST_": "8A5A00",
        "QUALITY_": "7F1D1D", "RM_": "355E3B", "ADD_": "526D82",
        "STEEL_": "4C566A", "SLAG_": "5B4B8A", "BASKET_": "6B5B3E",
        "PWR_": "9A3412", "O2_": "0369A1", "CINJ_": "374151",
        "EVTADD_": "7C3AED", "ANALYSIS_": "0F766E", "TAP_": "B45309",
        "DERIVED_": "475569", "RAW_": "334155",
    }
    for cell in worksheet[1]:
        prefix = next((item for item in group_colors if str(cell.value).startswith(item)), "META_")
        cell.fill = PatternFill("solid", fgColor=group_colors[prefix])
        cell.font = Font(color="FFFFFF", bold=True, name="Arial", size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    worksheet.row_dimensions[1].height = 36
    # Raw event cells can contain thousands of characters.  When wrap_text is
    # enabled Excel auto-expands the whole Run row to several screen heights.
    # Keep every Run compact; the full cell value remains available in the
    # formula bar and is not truncated in the workbook.
    for row_index in range(2, len(frame) + 2):
        worksheet.row_dimensions[row_index].height = 20

    sample_rows = min(len(frame), 200)
    for index, column in enumerate(frame.columns, start=1):
        letter = get_column_letter(index)
        lengths = [len(str(column))]
        if sample_rows:
            lengths.extend(
                len(str(value)) for value in frame[column].head(sample_rows)
                if not _is_missing(value)
            )
        width = 48 if column.startswith("RAW_") or column == "QUALITY_warning_text" else min(max(max(lengths, default=8) + 2, 11), 24)
        worksheet.column_dimensions[letter].width = width
        if column.startswith("RAW_") or column == "QUALITY_warning_text":
            for cell in worksheet[letter][1:]:
                cell.alignment = Alignment(vertical="center", wrap_text=False)

    def detail_column(column: str) -> bool:
        if column.startswith(("EVTADD_", "ANALYSIS_", "DERIVED_", "RAW_")):
            return True
        if column.startswith(("STEEL_", "SLAG_")):
            return column.endswith(
                ("_min", "_max", "_pass", "_margin_low", "_margin_high", "_normalized_position")
            )
        if column.startswith("BASKET_"):
            return bool(re.match(r"^BASKET_\d+_.+_t$", column))
        if column.startswith("PWR_"):
            return column.endswith("MW_duration_sec")
        return False

    for index, column in enumerate(frame.columns, start=1):
        if detail_column(str(column)):
            dimension = worksheet.column_dimensions[get_column_letter(index)]
            dimension.outlineLevel = 1
            dimension.hidden = True
            dimension.collapsed = False

    green = PatternFill("solid", fgColor="DCFCE7")
    red = PatternFill("solid", fgColor="FEE2E2")
    for index, column in enumerate(frame.columns, start=1):
        if column.startswith("QUALITY_") and column.endswith(("_pass", "_consistent")):
            letter = get_column_letter(index)
            target = f"{letter}2:{letter}{max(2, len(frame) + 1)}"
            worksheet.conditional_formatting.add(
                target, CellIsRule(operator="equal", formula=["1"], fill=green)
            )
            worksheet.conditional_formatting.add(
                target, CellIsRule(operator="equal", formula=["0"], fill=red)
            )

        if column in {"COST_per_tonne_usd", "PERF_time_min", "PERF_total_energy_kwh"}:
            letter = get_column_letter(index)
            for cell in worksheet[letter][1:]:
                cell.number_format = '#,##0.00'


def make_excel(
    runs_df: pd.DataFrame,
    logs_df: pd.DataFrame,
    ml_features_df: Optional[pd.DataFrame] = None,
) -> BytesIO:
    all_runs = build_all_runs_export_df(runs_df, logs_df, ml_features_df)
    return make_excel_from_frame(all_runs)


def make_excel_from_frame(all_runs: pd.DataFrame) -> BytesIO:
    """Serialize one already-built ALL_RUNS frame without rebuilding features."""

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        clean_dataframe_for_excel(all_runs).to_excel(writer, sheet_name="ALL_RUNS", index=False)
        apply_excel_formatting(writer, all_runs)
    output.seek(0)
    return output


def summarize_export_frame(frame: pd.DataFrame) -> dict[str, int]:
    def count_equals(column: str, value: str) -> int:
        if column not in frame:
            return 0
        return int(frame[column].fillna("").astype(str).eq(value).sum())

    return {
        "runs": len(frame),
        "columns": len(frame.columns),
        "merged_suspects": int(
            pd.to_numeric(
                frame.get("QUALITY_multi_run_merged_detected", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).sum()
        ),
        "rm_report": count_equals("QUALITY_rm_source", "REPORT")
        + count_equals("QUALITY_rm_source", "REPORT_AND_LOG_VERIFIED"),
        "rm_reconstructed": count_equals(
            "QUALITY_rm_source", "EVENT_LOG_RECONSTRUCTED"
        ),
        "add_report": count_equals("QUALITY_add_source", "REPORT")
        + count_equals("QUALITY_add_source", "REPORT_AND_LOG_VERIFIED"),
        "add_reconstructed": count_equals(
            "QUALITY_add_source", "EVENT_LOG_RECONSTRUCTED"
        ),
        "cost_mapping_warnings": int(
            pd.to_numeric(
                frame.get("COST_label_mismatch_detected", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).sum()
        ),
        "warning_runs": int(
            pd.to_numeric(
                frame.get("QUALITY_warning_count", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).gt(0).sum()
        ),
    }
