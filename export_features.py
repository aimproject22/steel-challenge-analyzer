"""Wide-format event features used by the single-sheet Excel export.

This module is intentionally independent from the database and Streamlit layers.
It converts one run's ordered event stream into deterministic, numeric columns
while retaining the complete raw event text.
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import Any, Iterable, Mapping, Optional

from canonical import classify_event, normalize_event_log, normalize_material_name


RAW_CELL_CHUNK_SIZE = 30_000

POWER_PATTERN = re.compile(
    r"\bPower(?:\s+set\s+to)?\s*:?\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*MW\b",
    re.I,
)
OXYGEN_PATTERN = re.compile(
    r"\bOxygen(?:\s+flow)?(?:\s+(?:changed|set)(?:\s+to)?)?\s*:?\s*\(?"
    r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(?:Nm(?:3|³)\s*/?\s*min)?",
    re.I,
)
CARBON_PATTERN = re.compile(
    r"\bCarbon(?:\s+injection)?(?:\s+flow)?(?:\s+(?:changed|set)(?:\s+to)?)?"
    r"\s*:?\s*\(?([-+]?\d[\d,]*(?:\.\d+)?)\s*(?:kg\s*/?\s*min)?",
    re.I,
)
MATERIAL_ITEM_PATTERN = re.compile(
    r"^\s*(.+?)(?:\s*:\s*|\s+)([-+]?\d[\d,]*(?:\.\d+)?)\s*(kg|t)\s*$",
    re.I,
)


def export_token(value: Any) -> str:
    """Return a stable Excel-column token without changing meaningful case."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
    return text or "Unknown"


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def ordered_logs(logs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and sort logs by event seconds, then original log number."""

    normalized = [
        normalize_event_log(log, index)
        for index, log in enumerate(logs or [], start=1)
    ]
    return sorted(
        normalized,
        key=lambda item: (
            float("inf")
            if item.get("event_seconds") is None
            else float(item["event_seconds"]),
            int(item.get("log_no") or 0),
        ),
    )


def _match_number(pattern: re.Pattern[str], text: str) -> Optional[float]:
    match = pattern.search(text)
    return _number(match.group(1)) if match else None


def _process_end_seconds(
    events: list[dict[str, Any]], reported_time_minutes: Any
) -> float:
    for item in events:
        if "tapping complete" in str(item.get("event") or "").casefold():
            seconds = _number(item.get("event_seconds"))
            if seconds is not None:
                return seconds
    reported = _number(reported_time_minutes)
    if reported is not None and reported >= 0:
        return reported * 60.0
    seconds = [_number(item.get("event_seconds")) for item in events]
    valid = [value for value in seconds if value is not None]
    return max(valid) if valid else 0.0


def _integrate_setpoints(
    events: list[dict[str, Any]],
    pattern: re.Pattern[str],
    end_seconds: float,
) -> dict[str, Any]:
    changes: list[tuple[float, float]] = []
    for item in events:
        seconds = _number(item.get("event_seconds"))
        value = _match_number(pattern, str(item.get("event") or ""))
        if seconds is not None and value is not None:
            changes.append((seconds, value))

    durations: dict[float, float] = {}
    integrated = 0.0
    on_seconds = 0.0
    off_seconds = 0.0
    for index, (start, value) in enumerate(changes):
        next_start = changes[index + 1][0] if index + 1 < len(changes) else end_seconds
        duration = max(0.0, next_start - start)
        durations[value] = durations.get(value, 0.0) + duration
        integrated += value * duration
        if value > 0:
            on_seconds += duration
        else:
            off_seconds += duration

    positive = [value for _, value in changes if value > 0]
    first_on = next((seconds for seconds, value in changes if value > 0), None)
    last_off = next(
        (seconds for seconds, value in reversed(changes) if value == 0), None
    )
    elapsed = sum(durations.values())
    return {
        "changes": changes,
        "durations": durations,
        "first_event": changes[0][0] if changes else None,
        "first_on": first_on,
        "last_change": changes[-1][0] if changes else None,
        "last_off": last_off,
        "max": max(positive) if positive else (0.0 if changes else None),
        "min_nonzero": min(positive) if positive else None,
        "change_count": len(changes),
        "on_seconds": on_seconds,
        "off_seconds": off_seconds,
        "integrated": integrated,
        "weighted_average": integrated / elapsed if elapsed > 0 else None,
    }


def _parse_materials(
    text: str, *, addition: bool = False
) -> list[tuple[str, float, str]]:
    """Parse colon or whitespace separated material amounts from one event."""

    parsed: list[tuple[str, float, str]] = []
    for part in str(text or "").split(";"):
        match = MATERIAL_ITEM_PATTERN.match(part)
        if not match:
            continue
        name, number, unit = match.groups()
        parsed.append(
            (
                normalize_material_name(name, addition=addition),
                float(number.replace(",", "")),
                unit.casefold(),
            )
        )
    return parsed


def _compact_event(event: str) -> Optional[str]:
    lower = event.casefold()
    if "simulation rate" in lower or "selected user" in lower or "selected steel" in lower:
        return None
    power = _match_number(POWER_PATTERN, event)
    if power is not None:
        return f"POWER[{power:g}MW]"
    oxygen = _match_number(OXYGEN_PATTERN, event)
    if oxygen is not None and "oxygen" in lower:
        return f"O2[{oxygen:g}Nm3/min]"
    carbon = _match_number(CARBON_PATTERN, event)
    if carbon is not None and "carbon" in lower and "addition" not in lower:
        return f"CINJ[{carbon:g}]"
    if "scrap basket added" in lower:
        materials = _parse_materials(event.split(":", 1)[1] if ":" in event else event)
        payload = " + ".join(f"{name} {value:g}{unit}" for name, value, unit in materials)
        return f"CHARGE[{payload or event}]"
    if "additions:" in lower:
        materials = _parse_materials(event.split(":", 1)[1], addition=True)
        payload = " + ".join(f"{name} {value:g}{unit}" for name, value, unit in materials)
        return f"ADD[{payload or event}]"
    if "analysis requested" in lower:
        return "ANALYSIS_REQUEST"
    if "analysis received" in lower:
        return "ANALYSIS_RECEIVED"
    if "tapping start" in lower:
        return "TAPPING_START"
    if "tapping complete" in lower:
        return "TAPPING_COMPLETE"
    return f"EVENT[{event}]"


def _is_known_event(event: str) -> bool:
    lower = event.casefold()
    return bool(
        classify_event(event) != "OTHER"
        or "carbon" in lower
        or "simulation rate" in lower
        or "selected user" in lower
        or "selected steel" in lower
    )


def split_raw_event_log(raw_text: str) -> dict[str, str]:
    """Split without data loss below Excel's 32,767-character cell limit."""

    if not raw_text:
        return {"RAW_event_log_01": ""}
    return {
        f"RAW_event_log_{index:02d}": raw_text[start : start + RAW_CELL_CHUNK_SIZE]
        for index, start in enumerate(range(0, len(raw_text), RAW_CELL_CHUNK_SIZE), start=1)
    }


def extract_event_export_features(
    logs: Iterable[Mapping[str, Any]],
    *,
    reported_time_minutes: Any = None,
    reported_energy_kwh: Any = None,
) -> dict[str, Any]:
    """Build dynamic wide-format process features for one simulation run."""

    events = ordered_logs(logs)
    end_seconds = _process_end_seconds(events, reported_time_minutes)
    result: dict[str, Any] = {}
    warnings: list[str] = []

    power = _integrate_setpoints(events, POWER_PATTERN, end_seconds)
    has_power = bool(power["changes"])
    result.update(
        {
            "PWR_first_event_sec": power["first_event"],
            "PWR_first_on_sec": power["first_on"],
            "PWR_last_change_sec": power["last_change"],
            "PWR_max_mw": power["max"] if has_power else None,
            "PWR_min_nonzero_mw": power["min_nonzero"],
            "PWR_change_count": power["change_count"],
            "PWR_total_on_sec": power["on_seconds"] if has_power else None,
            "PWR_total_off_sec": power["off_seconds"] if has_power else None,
            "PWR_time_weighted_avg_mw": power["weighted_average"],
            "PWR_setpoint_integral_MW_sec": power["integrated"] if power["changes"] else None,
            "PWR_setpoint_integral_MW_min": power["integrated"] / 60.0 if power["changes"] else None,
            "PWR_setpoint_equivalent_MWh": power["integrated"] / 3600.0 if power["changes"] else None,
        }
    )
    for value, duration in power["durations"].items():
        result[f"PWR_{value:g}MW_duration_sec"] = duration

    oxygen = _integrate_setpoints(events, OXYGEN_PATTERN, end_seconds)
    has_oxygen = bool(oxygen["changes"])
    result.update(
        {
            "O2_used": int(has_oxygen),
            "O2_first_on_sec": oxygen["first_on"],
            "O2_last_off_sec": oxygen["last_off"],
            "O2_total_on_sec": oxygen["on_seconds"] if has_oxygen else None,
            "O2_max_flow_Nm3_min": oxygen["max"] if has_oxygen else None,
            "O2_time_weighted_avg_flow_Nm3_min": oxygen["weighted_average"],
            "O2_change_count": oxygen["change_count"],
            "O2_integrated_Nm3": oxygen["integrated"] / 60.0 if has_oxygen else None,
        }
    )

    carbon_events = [
        item
        for item in events
        if "carbon" in str(item.get("event") or "").casefold()
        and "addition" not in str(item.get("event") or "").casefold()
    ]
    carbon = _integrate_setpoints(carbon_events, CARBON_PATTERN, end_seconds)
    has_carbon = bool(carbon["changes"])
    result.update(
        {
            "CINJ_used": int(has_carbon),
            "CINJ_first_on_sec": carbon["first_on"],
            "CINJ_last_off_sec": carbon["last_off"],
            "CINJ_total_on_sec": carbon["on_seconds"] if has_carbon else None,
            "CINJ_max_flow": carbon["max"] if has_carbon else None,
            "CINJ_time_weighted_avg_flow": carbon["weighted_average"],
            "CINJ_integrated_kg": carbon["integrated"] / 60.0 if has_carbon else None,
            "CINJ_change_count": carbon["change_count"],
        }
    )

    basket_times: list[float] = []
    basket_total = 0.0
    addition_times: list[float] = []
    addition_by_name: dict[str, list[float]] = {}
    addition_amounts: dict[str, float] = {}
    analysis_requests: deque[float] = deque()
    analysis_received: list[float] = []
    analysis_request_all: list[float] = []
    analysis_waits: list[float] = []
    tapping_start: Optional[float] = None
    tapping_complete: Optional[float] = None
    compact_sequence: list[str] = []
    raw_lines: list[str] = []
    unknown_lines: list[str] = []

    for item in events:
        event = str(item.get("event") or "")
        lower = event.casefold()
        seconds = _number(item.get("event_seconds"))
        event_time = str(item.get("event_time") or item.get("time") or "")
        raw_lines.append(f"{event_time},{event}")
        compact = _compact_event(event)
        if compact:
            compact_sequence.append(f"{event_time} {compact}")
        if not _is_known_event(event):
            unknown_lines.append(f"{event_time},{event}")

        if "scrap basket added" in lower and seconds is not None:
            basket_times.append(seconds)
            basket_index = len(basket_times)
            result[f"BASKET_{basket_index}_time_sec"] = seconds
            result[f"BASKET_{basket_index}_time_min"] = seconds / 60.0
            materials = _parse_materials(event.split(":", 1)[1] if ":" in event else event)
            mass = 0.0
            basket_materials: dict[str, float] = {}
            for name, value, unit in materials:
                tonnes = value if unit == "t" else value / 1000.0
                mass += tonnes
                basket_materials[name] = basket_materials.get(name, 0.0) + tonnes
            for name, tonnes in basket_materials.items():
                token = export_token(name)
                result[f"BASKET_{basket_index}_{token}_t"] = tonnes
                total_key = f"BASKET_{token}_total_t"
                result[total_key] = result.get(total_key, 0.0) + tonnes
            result[f"BASKET_{basket_index}_mass_t"] = mass
            basket_total += mass

        if "additions:" in lower and seconds is not None:
            addition_times.append(seconds)
            for name, amount, unit in _parse_materials(
                event.split(":", 1)[1], addition=True
            ):
                addition_by_name.setdefault(name, []).append(seconds)
                amount_kg = amount * 1000.0 if unit == "t" else amount
                addition_amounts[name] = addition_amounts.get(name, 0.0) + amount_kg

        if "analysis requested" in lower and seconds is not None:
            analysis_requests.append(seconds)
            analysis_request_all.append(seconds)
        elif "analysis received" in lower and seconds is not None:
            analysis_received.append(seconds)
            if analysis_requests:
                analysis_waits.append(max(0.0, seconds - analysis_requests.popleft()))
            else:
                warnings.append("Analysis received without matching request")
        if "tapping start" in lower and tapping_start is None:
            tapping_start = seconds
        if "tapping complete" in lower and tapping_complete is None:
            tapping_complete = seconds

    if analysis_requests:
        warnings.append(f"{len(analysis_requests)} analysis request(s) without result")

    result["BASKET_count"] = len(basket_times)
    result["BASKET_total_charged_t"] = basket_total if basket_times else None
    for index in range(len(basket_times) - 1):
        result[f"BASKET_{index + 1}_to_{index + 2}_gap_sec"] = (
            basket_times[index + 1] - basket_times[index]
        )

    result.update(
        {
            "EVTADD_first_addition_sec": addition_times[0] if addition_times else None,
            "EVTADD_last_addition_sec": addition_times[-1] if addition_times else None,
            "EVTADD_event_count": len(addition_times),
        }
    )
    for name, times in addition_by_name.items():
        token = export_token(name)
        result[f"EVTADD_{token}_first_sec"] = times[0]
        result[f"EVTADD_{token}_last_sec"] = times[-1]
        result[f"EVTADD_{token}_event_count"] = len(times)
        result[f"EVTADD_{token}_total_kg"] = addition_amounts.get(name)
    result["EVTADD_first_addition_to_tapping_sec"] = (
        tapping_start - addition_times[0]
        if tapping_start is not None and addition_times
        else None
    )
    result["EVTADD_last_addition_to_tapping_sec"] = (
        tapping_start - addition_times[-1]
        if tapping_start is not None and addition_times
        else None
    )

    result.update(
        {
            "ANALYSIS_request_count": len(analysis_request_all),
            "ANALYSIS_received_count": len(analysis_received),
            "ANALYSIS_first_request_sec": analysis_request_all[0] if analysis_request_all else None,
            "ANALYSIS_first_received_sec": analysis_received[0] if analysis_received else None,
            "ANALYSIS_last_request_sec": analysis_request_all[-1] if analysis_request_all else None,
            "ANALYSIS_last_received_sec": analysis_received[-1] if analysis_received else None,
            "ANALYSIS_avg_wait_sec": sum(analysis_waits) / len(analysis_waits) if analysis_waits else None,
            "ANALYSIS_max_wait_sec": max(analysis_waits) if analysis_waits else None,
            "ANALYSIS_total_wait_sec": sum(analysis_waits) if analysis_waits else None,
            "TAP_start_sec": tapping_start,
            "TAP_complete_sec": tapping_complete,
            "TAP_duration_sec": (
                tapping_complete - tapping_start
                if tapping_start is not None and tapping_complete is not None
                else None
            ),
            "TAP_start_min": tapping_start / 60.0 if tapping_start is not None else None,
            "TAP_complete_min": tapping_complete / 60.0 if tapping_complete is not None else None,
            "TAP_duration_min": (
                (tapping_complete - tapping_start) / 60.0
                if tapping_start is not None and tapping_complete is not None
                else None
            ),
        }
    )

    first_power = power["first_on"]
    last_basket = basket_times[-1] if basket_times else None
    first_addition = addition_times[0] if addition_times else None
    last_addition = addition_times[-1] if addition_times else None
    result.update(
        {
            "DERIVED_charge_to_first_power_sec": (
                first_power - basket_times[0]
                if first_power is not None and basket_times
                else None
            ),
            "DERIVED_first_power_to_second_basket_sec": (
                basket_times[1] - first_power
                if first_power is not None and len(basket_times) > 1
                else None
            ),
            "DERIVED_last_basket_to_tapping_sec": (
                tapping_start - last_basket
                if tapping_start is not None and last_basket is not None
                else None
            ),
            "DERIVED_last_basket_to_first_addition_sec": (
                first_addition - last_basket
                if first_addition is not None and last_basket is not None
                else None
            ),
            "DERIVED_first_addition_to_tapping_sec": (
                tapping_start - first_addition
                if tapping_start is not None and first_addition is not None
                else None
            ),
            "DERIVED_last_addition_to_tapping_sec": (
                tapping_start - last_addition
                if tapping_start is not None and last_addition is not None
                else None
            ),
            "DERIVED_analysis_to_tapping_sec": (
                tapping_start - analysis_received[-1]
                if tapping_start is not None and analysis_received
                else None
            ),
            "DERIVED_process_end_sec": end_seconds,
            "DERIVED_process_end_min": end_seconds / 60.0,
            "DERIVED_power_utilization_pct": (
                power["on_seconds"] / end_seconds * 100.0 if end_seconds > 0 else None
            ),
        }
    )

    result["QUALITY_unknown_event_count"] = len(unknown_lines)
    result["RAW_unknown_events"] = "\n".join(unknown_lines)
    result["RAW_event_sequence"] = " -> ".join(compact_sequence)
    result.update(split_raw_event_log("\n".join(raw_lines)))
    result["_warnings"] = warnings
    return result
