# feature_engineering.py
# -*- coding: utf-8 -*-

import re


def time_to_minutes(time_text):
    try:
        h, m, s = map(int, time_text.split(":"))
        return h * 60 + m + s / 60
    except Exception:
        return None


def extract_features_from_logs(logs):
    features = {
        "feature > log_count": len(logs),

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

        "feature > carbon_added_kg_log": 0,
        "feature > lime_added_kg_log": 0,
        "feature > dolomite_added_kg_log": 0,
        "feature > chrome_carbure_low_s_added_kg_log": 0,
        "feature > silico_chromium_added_kg_log": 0,
    }

    for log in logs:
        event = log["event"]
        t_min = time_to_minutes(log["time"])

        if "Power set to" in event:
            features["feature > power_change_count"] += 1

            if "120 MW" in event and features["feature > power_120_first_min"] is None:
                features["feature > power_120_first_min"] = t_min

            if "75 MW" in event and features["feature > power_75_first_min"] is None:
                features["feature > power_75_first_min"] = t_min

            if "0 MW" in event and features["feature > power_0_first_min"] is None:
                features["feature > power_0_first_min"] = t_min

        if "Oxygen flow changed" in event:
            features["feature > oxygen_change_count"] += 1

            if features["feature > oxygen_first_min"] is None:
                features["feature > oxygen_first_min"] = t_min

            if "150" in event and features["feature > oxygen_150_first_min"] is None:
                features["feature > oxygen_150_first_min"] = t_min

            if "(0" in event or " 0 " in event:
                if features["feature > oxygen_0_first_min"] is None:
                    features["feature > oxygen_0_first_min"] = t_min

        if "Scrap basket added" in event:
            features["feature > scrap_basket_count"] += 1

        if "Analysis Requested" in event:
            features["feature > analysis_requested_count"] += 1

        if "Analysis received" in event:
            features["feature > analysis_received_count"] += 1

        if "Tapping start" in event:
            features["feature > tapping_start_min"] = t_min

        if "Tapping complete" in event:
            features["feature > tapping_complete_min"] = t_min

        if "Additions:" in event:
            features["feature > addition_event_count"] += 1

            patterns = {
                "feature > carbon_added_kg_log": r"Carbon:\s*(\d+)\s*kg",
                "feature > lime_added_kg_log": r"Lime:\s*(\d+)\s*kg",
                "feature > dolomite_added_kg_log": r"Dolomite:\s*(\d+)\s*kg",
                "feature > chrome_carbure_low_s_added_kg_log": r"Chrome-Carbure Low S:\s*(\d+)\s*kg",
                "feature > silico_chromium_added_kg_log": r"Silico-Chromium:\s*(\d+)\s*kg",
            }

            for feature_name, pattern in patterns.items():
                match = re.search(pattern, event)
                if match:
                    features[feature_name] += int(match.group(1))

    if (
        features["feature > tapping_start_min"] is not None
        and features["feature > tapping_complete_min"] is not None
    ):
        features["feature > tapping_duration_min"] = (
            features["feature > tapping_complete_min"]
            - features["feature > tapping_start_min"]
        )

    return features