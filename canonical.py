"""Canonical Steel Challenge data structures shared by all input sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import unquote
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")

SECTION_RUN = "Run Information"
SECTION_SIM = "Simulation Settings"
SECTION_COST = "Cost Breakdown"
SECTION_STEEL = "Steel Composition"
SECTION_RAW = "Raw Materials"
SECTION_ADD = "Additions"
SECTION_SLAG = "Slag Composition"

SECTION_ALIASES = {
    "run information": SECTION_RUN,
    "simulation settings": SECTION_SIM,
    "simulation setting": SECTION_SIM,
    "cost breakdown": SECTION_COST,
    "steel composition": SECTION_STEEL,
    "steel composition / wt%": SECTION_STEEL,
    "steel composition/wt%": SECTION_STEEL,
    "raw materials": SECTION_RAW,
    "additions": SECTION_ADD,
    "slag composition": SECTION_SLAG,
    "event log": "Event Log",
}

KEY_ALIASES = {
    "status": "Status",
    "time(in minutes)": "Time (in minutes)",
    "time (in minutes)": "Time (in minutes)",
    "intemal low alloyed": "Internal Low Alloyed",
    "internal low alloyed": "Internal Low Alloyed",
    "plate and structural": "Plate and Structural",
    "eafscrap type05": "eafScrapType05",
    "eafscraptype05": "eafScrapType05",
    "eafscrap type10": "eafScrapType10",
    "eafscraptype10": "eafScrapType10",
    "turnnings": "Turnings",
    "turnings": "Turnings",
    "user id": "User Id",
}

HEADER_WORDS = {
    "element",
    "current",
    "min",
    "max",
    "name",
    "qty [t]",
    "qty[t]",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_key(value: Any) -> str:
    text = normalize_text(value)
    return KEY_ALIASES.get(text.casefold(), text)


def canonical_section(value: Any) -> Optional[str]:
    text = normalize_text(value).rstrip(":").casefold()
    return SECTION_ALIASES.get(text)


def normalize_value(value: Any) -> Any:
    """Normalize a scalar while preserving non-numeric text."""

    text = normalize_text(value)
    if not text:
        return None

    cleaned = text.replace("$", "").replace(",", "")
    cleaned = re.sub(r"\s*(kWh/t|kWh|kg|MW)\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"(?<=\d)\s*t\s*$", "", cleaned, flags=re.I)
    cleaned = cleaned.strip()

    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned):
        number = float(cleaned)
        return int(number) if number.is_integer() else number
    return text


def normalize_datetime(value: Any, timezone: ZoneInfo = SEOUL) -> Optional[datetime]:
    """Parse Steel Challenge's DD/MM/YYYY timestamp as timezone-aware."""

    text = normalize_text(value)
    if not text:
        return None

    formats = (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
    )
    for date_format in formats:
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=timezone)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"지원하지 않는 날짜 형식입니다: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def event_time_to_seconds(value: Any) -> Optional[float]:
    text = normalize_text(value)
    try:
        hours, minutes, seconds = [float(part) for part in text.split(":")]
    except (TypeError, ValueError):
        return None
    return hours * 3600 + minutes * 60 + seconds


def classify_event(event: Any) -> str:
    text = normalize_text(event).casefold()
    if "scrap basket" in text:
        return "SCRAP"
    if "power" in text:
        return "POWER"
    if "oxygen" in text:
        return "OXYGEN"
    if "addition" in text:
        return "ADDITION"
    if "analysis" in text:
        return "ANALYSIS"
    if "tapping" in text:
        return "TAPPING"
    return "OTHER"


def normalize_event_log(log: Mapping[str, Any], log_no: int) -> Dict[str, Any]:
    event_time = normalize_text(log.get("event_time") or log.get("time"))
    event = unquote(normalize_text(log.get("event")))
    seconds = log.get("event_seconds")
    if seconds is None:
        seconds = event_time_to_seconds(event_time)
    return {
        "log_no": int(log.get("log_no") or log_no),
        # Keep the legacy key because feature_engineering.py and old exports use it.
        "time": event_time,
        "event_time": event_time,
        "event_seconds": seconds,
        "event": event,
        "category": log.get("category") or classify_event(event),
    }


@dataclass
class ParsedRun:
    data: Dict[str, Any]
    logs: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.logs = [
            normalize_event_log(log, index)
            for index, log in enumerate(self.logs, start=1)
        ]

    def to_legacy_tuple(self) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        return self.data, self.logs


def add_measurement(
    data: Dict[str, Any],
    section: str,
    name: Any,
    current: Any,
    minimum: Any = None,
    maximum: Any = None,
) -> None:
    """Store Current/Min/Max and retain the historical Current-only key."""

    key = normalize_key(name)
    if not key or key.casefold() in HEADER_WORDS:
        return
    current_value = normalize_value(current)
    minimum_value = normalize_value(minimum)
    maximum_value = normalize_value(maximum)

    data[f"{section} > {key}"] = current_value
    data[f"{section} > {key} > Current"] = current_value
    data[f"{section} > {key} > Min"] = minimum_value
    data[f"{section} > {key} > Max"] = maximum_value


def build_structured_sections(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Build nested composition objects without removing flat compatibility keys."""

    result: Dict[str, Any] = {}
    for section, target_name in (
        (SECTION_STEEL, "steel_composition"),
        (SECTION_SLAG, "slag_composition"),
    ):
        section_result: Dict[str, Dict[str, Any]] = {}
        prefix = f"{section} > "
        for key, value in data.items():
            if not key.startswith(prefix) or not key.endswith(" > Current"):
                continue
            element = key[len(prefix) : -len(" > Current")]
            section_result[element] = {
                "current": value,
                "min": data.get(f"{section} > {element} > Min"),
                "max": data.get(f"{section} > {element} > Max"),
            }
        result[target_name] = section_result
    return result

