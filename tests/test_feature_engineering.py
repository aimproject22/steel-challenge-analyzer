from __future__ import annotations

import pytest

from feature_engineering import extract_features_from_logs


def test_sequence_features_and_exact_zero_detection() -> None:
    events = [
        {"time": "00:00:00", "event": "Scrap basket added with: No1: 10t"},
        {"time": "00:01:00", "event": "Power set to: 120 MW"},
        {"time": "00:02:00", "event": "Additions: Lime: 20 kg; Iron Oxide: 5 kg"},
        {"time": "00:03:00", "event": "Scrap basket added with: No2: 10t"},
        {"time": "00:04:00", "event": "Power set to: 0 MW"},
        {"time": "00:05:00", "event": "Oxygen flow changed: (150 Nm3/min)"},
        {"time": "00:06:00", "event": "Analysis Requested"},
        {"time": "00:07:00", "event": "Tapping start"},
        {"time": "00:08:00", "event": "Oxygen flow changed: (0 Nm3/min)"},
        {"time": "00:10:00", "event": "Tapping complete"},
    ]
    data = {
        "Steel Composition > C > Current": 0.08,
        "Steel Composition > C > Min": 0.1,
        "Steel Composition > C > Max": 0.12,
        "Slag Composition > Basicity > Current": 2.0,
        "Slag Composition > Basicity > Min": 1.5,
        "Slag Composition > Basicity > Max": 2.5,
    }

    features = extract_features_from_logs(events, data)

    assert features["feature > power_120_first_min"] == pytest.approx(1)
    assert features["feature > power_0_first_min"] == pytest.approx(4)
    assert features["feature > power_on_total_duration"] == pytest.approx(3)
    assert features["feature > power_off_total_duration"] == pytest.approx(6)
    assert features["feature > oxygen_on_total_duration"] == pytest.approx(3)
    assert features["feature > time_first_scrap"] == pytest.approx(0)
    assert features["feature > time_second_scrap"] == pytest.approx(3)
    assert features["feature > basket_interval_1_2"] == pytest.approx(3)
    assert features["feature > power_start_to_second_basket"] == pytest.approx(2)
    assert features["feature > tapping_duration_min"] == pytest.approx(3)
    assert features["feature > analysis_to_tapping_min"] == pytest.approx(1)
    assert features["feature > total_lime_kg"] == pytest.approx(20)
    assert features["feature > total_iron_oxide_kg"] == pytest.approx(5)
    assert features["feature > composition_violation_count"] == 1
    assert features["feature > slag_violation_count"] == 0
