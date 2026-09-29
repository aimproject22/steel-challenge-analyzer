# -*- coding: utf-8 -*-
"""Feature extraction from Steel Challenge event sequences."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

from canonical import event_time_to_seconds, normalize_event_log


def time_to_minutes(time_text: Any) -> Optional[float]:
    seconds = event_time_to_seconds(time_text)
    return None if seconds is None else seconds / 60.0


def _ordered_logs(logs: Iterable[Mapping[str, Any]]) -> list[dict]:
    normalized = [
        normalize_event_log(log, index)
        for index, log in enumerate(logs or [], start=1)
    ]
    return sorted(
        normalized,
        key=lambda item: (
            float("inf") if item["event_seconds"] is None else item["event_seconds"],
            item["log_no"],
        ),
    )


def _first_time(events: list[dict], predicate) -> Optional[float]:
    for item in events:
        if predicate(item["event"]):
            return time_to_minutes(item["time"])
    return None


def _extract_setpoint(event: str, pattern: str) -> Optional[float]:
    match = re.search(pattern, event, flags=re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _state_durations(
    events: list[dict],
    pattern: str,
    end_seconds: float,
) -> tuple[float, float]:
    on_seconds = 0.0
    off_seconds = 0.0
    last_time: Optional[float] = None
    last_value: Optional[float] = None

    for item in events:
        seconds = item.get("event_seconds")
        value = _extract_setpoint(item["event"], pattern)
        if seconds is None or value is None:
            continue
        seconds = float(seconds)
        if last_time is not None and last_value is not None and seconds >= last_time:
            duration = seconds - last_time
            if last_value > 0:
                on_seconds += duration
            else:
                off_seconds += duration
        last_time = seconds
        last_value = value

    if last_time is not None and last_value is not None and end_seconds >= last_time:
        duration = end_seconds - last_time
        if last_value > 0:
            on_seconds += duration
        else:
            off_seconds += duration
    return on_seconds / 60.0, off_seconds / 60.0


def _addition_totals(events: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in events:
        event = item["event"]
        if "addition" not in event.casefold() or ":" not in event:
            continue
        payload = event.split(":", 1)[1]
        for name, number in re.findall(
            r"(?:^|;)\s*([^:;]+?)\s*:\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*kg\b",
            payload,
            flags=re.I,
        ):
            canonical_name = re.sub(r"\s+", " ", name).strip()
            totals[canonical_name] = totals.get(canonical_name, 0.0) + float(
                number.replace(",", "")
            )
    return totals


def _table_addition(data: Optional[Mapping[str, Any]], name: str) -> float:
    if not data:
        return 0.0
    value = data.get(f"Additions > {name}")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _spec_features(data: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not data:
        return {
            "feature > composition_violation_count": 0,
            "feature > slag_violation_count": 0,
        }

    result: dict[str, Any] = {}
    for section, count_name in (
        ("Steel Composition", "composition_violation_count"),
        ("Slag Composition", "slag_violation_count"),
    ):
        violations = 0
        prefix = f"{section} > "
        for key, current in data.items():
            if not key.startswith(prefix) or not key.endswith(" > Current"):
                continue
            element = key[len(prefix) : -len(" > Current")]
            minimum = data.get(f"{section} > {element} > Min")
            maximum = data.get(f"{section} > {element} > Max")
            try:
                current_number = float(current)
            except (TypeError, ValueError):
                continue
            within_spec = True
            try:
                if minimum is not None and current_number < float(minimum):
                    within_spec = False
            except (TypeError, ValueError):
                pass
            try:
                if maximum is not None and current_number > float(maximum):
                    within_spec = False
            except (TypeError, ValueError):
                pass
            if not within_spec:
                violations += 1
            safe_element = re.sub(r"[^0-9A-Za-z]+", "_", element).strip("_")
            result[f"feature > {section.lower().replace(' ', '_')}_{safe_element}_within_spec"] = int(
                within_spec
            )
        result[f"feature > {count_name}"] = violations
    return result


def extract_features_from_logs(
    logs: Iterable[Mapping[str, Any]],
    data: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return legacy and extended features without inventing absent event values."""

    events = _ordered_logs(logs)
    features: dict[str, Any] = {
        "feature > log_count": len(events),
        "feature > event_count": len(events),
        "feature > power_change_count": 0,
        "feature > oxygen_change_count": 0,
        "feature > scrap_basket_count": 0,
        "feature > addition_event_count": 0,
        "feature > analysis_requested_count": 0,
        "feature > analysis_received_count": 0,
        "feature > power_120_first_min": None,
        "feature > power_75_first_min": None,
        "feature > power_0_first_min": None,
        "feature > oxygen_first_min": None,
        "feature > oxygen_150_first_min": None,
        "feature > oxygen_0_first_min": None,
        "feature > tapping_start_min": None,
        "feature > tapping_complete_min": None,
        "feature > tapping_duration_min": None,
        "feature > carbon_added_kg_log": 0.0,
        "feature > lime_added_kg_log": 0.0,
        "feature > dolomite_added_kg_log": 0.0,
        "feature > chrome_carbure_low_s_added_kg_log": 0.0,
        "feature > silico_chromium_added_kg_log": 0.0,
        "feature > power_on_total_duration": None,
        "feature > power_off_total_duration": None,
        "feature > oxygen_on_total_duration": None,
        "feature > time_first_scrap": None,
        "feature > time_second_scrap": None,
        "feature > time_first_addition": None,
        "feature > time_first_analysis": None,
        "feature > analysis_to_tapping_min": None,
        "feature > basket_interval_1_2": None,
        "feature > power_start_to_second_basket": None,
        "feature > total_carbon_kg": 0.0,
        "feature > total_lime_kg": 0.0,
        "feature > total_dolomite_kg": 0.0,
        "feature > total_iron_oxide_kg": 0.0,
    }

    scrap_times: list[float] = []
    power_start: Optional[float] = None
    analysis_time: Optional[float] = None

    for item in events:
        event = item["event"]
        event_lower = event.casefold()
        t_min = time_to_minutes(item["time"])

        power = _extract_setpoint(event, r"Power\s+set\s+to\s*:\s*([-+]?\d+(?:\.\d+)?)\s*MW")
        if power is not None:
            features["feature > power_change_count"] += 1
            if power > 0 and power_start is None:
                power_start = t_min
            if power == 120 and features["feature > power_120_first_min"] is None:
                features["feature > power_120_first_min"] = t_min
            if power == 75 and features["feature > power_75_first_min"] is None:
                features["feature > power_75_first_min"] = t_min
            if power == 0 and features["feature > power_0_first_min"] is None:
                features["feature > power_0_first_min"] = t_min

        oxygen = _extract_setpoint(
            event,
            r"Oxygen(?:\s+flow)?\s+(?:changed|set)(?:\s+to)?\s*:?\s*\(?([-+]?\d+(?:\.\d+)?)",
        )
        if oxygen is not None:
            features["feature > oxygen_change_count"] += 1
            if features["feature > oxygen_first_min"] is None:
                features["feature > oxygen_first_min"] = t_min
            if oxygen == 150 and features["feature > oxygen_150_first_min"] is None:
                features["feature > oxygen_150_first_min"] = t_min
            if oxygen == 0 and features["feature > oxygen_0_first_min"] is None:
                features["feature > oxygen_0_first_min"] = t_min

        if "scrap basket added" in event_lower:
            features["feature > scrap_basket_count"] += 1
            if t_min is not None:
                scrap_times.append(t_min)
        if "analysis requested" in event_lower:
            features["feature > analysis_requested_count"] += 1
            if analysis_time is None:
                analysis_time = t_min
        if "analysis received" in event_lower:
            features["feature > analysis_received_count"] += 1
        if "tapping start" in event_lower and features["feature > tapping_start_min"] is None:
            features["feature > tapping_start_min"] = t_min
        if "tapping complete" in event_lower and features["feature > tapping_complete_min"] is None:
            features["feature > tapping_complete_min"] = t_min
        if "additions:" in event_lower:
            features["feature > addition_event_count"] += 1
            if features["feature > time_first_addition"] is None:
                features["feature > time_first_addition"] = t_min

    addition_totals = _addition_totals(events)
    lookup = {key.casefold(): value for key, value in addition_totals.items()}
    legacy_materials = {
        "carbon": "feature > carbon_added_kg_log",
        "lime": "feature > lime_added_kg_log",
        "dolomite": "feature > dolomite_added_kg_log",
        "chrome-carbure low s": "feature > chrome_carbure_low_s_added_kg_log",
        "silico-chromium": "feature > silico_chromium_added_kg_log",
    }
    for material, feature_name in legacy_materials.items():
        features[feature_name] = lookup.get(material, 0.0)

    total_materials = {
        "Carbon": "feature > total_carbon_kg",
        "Lime": "feature > total_lime_kg",
        "Dolomite": "feature > total_dolomite_kg",
        "Iron Oxide": "feature > total_iron_oxide_kg",
    }
    for material, feature_name in total_materials.items():
        features[feature_name] = lookup.get(
            material.casefold(), _table_addition(data, material)
        )

    tapping_start = features["feature > tapping_start_min"]
    tapping_complete = features["feature > tapping_complete_min"]
    if tapping_start is not None and tapping_complete is not None:
        features["feature > tapping_duration_min"] = tapping_complete - tapping_start
    if analysis_time is not None and tapping_start is not None:
        features["feature > analysis_to_tapping_min"] = tapping_start - analysis_time
    features["feature > time_first_analysis"] = analysis_time

    if scrap_times:
        features["feature > time_first_scrap"] = scrap_times[0]
    if len(scrap_times) > 1:
        features["feature > time_second_scrap"] = scrap_times[1]
        features["feature > basket_interval_1_2"] = scrap_times[1] - scrap_times[0]
        if power_start is not None:
            features["feature > power_start_to_second_basket"] = scrap_times[1] - power_start

    valid_seconds = [
        float(item["event_seconds"])
        for item in events
        if item.get("event_seconds") is not None
    ]
    if valid_seconds:
        end_seconds = max(valid_seconds)
        power_on, power_off = _state_durations(
            events,
            r"Power\s+set\s+to\s*:\s*([-+]?\d+(?:\.\d+)?)\s*MW",
            end_seconds,
        )
        oxygen_on, _ = _state_durations(
            events,
            r"Oxygen(?:\s+flow)?\s+(?:changed|set)(?:\s+to)?\s*:?\s*\(?([-+]?\d+(?:\.\d+)?)",
            end_seconds,
        )
        features["feature > power_on_total_duration"] = power_on
        features["feature > power_off_total_duration"] = power_off
        features["feature > oxygen_on_total_duration"] = oxygen_on

    features.update(_spec_features(data))
    return features
