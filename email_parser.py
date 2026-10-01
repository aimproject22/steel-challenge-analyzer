"""Parse Steel Challenge result emails from HTML or plain-text bodies."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup

from canonical import (
    SECTION_ADD,
    SECTION_COST,
    SECTION_RAW,
    SECTION_RUN,
    SECTION_SIM,
    SECTION_SLAG,
    SECTION_STEEL,
    add_measurement,
    build_structured_sections,
    canonical_section,
    normalize_datetime,
    normalize_event_log,
    normalize_key,
    normalize_text,
    normalize_value,
)
from parser import _classify_table, parse_current_min_max_rows, parse_key_value_rows


class EmailParseError(ValueError):
    """Raised when neither HTML nor plain text contains a valid result."""


PROCESS_EAF = "Electric Arc Furnace"
PROCESS_SECONDARY = "Secondary Steelmaking"


def detect_process_type(*values: Any) -> str:
    """Identify the Steel University simulation without trusting the sender."""

    text = " ".join(normalize_text(value) for value in values if value).casefold()
    if "secondary steelmaking" in text:
        return PROCESS_SECONDARY
    if "electric arc furnace" in text:
        return PROCESS_EAF
    # The original platform and historical DOCX fixtures are EAF results.
    return PROCESS_EAF


KEY_VALUE_SECTIONS = {SECTION_RUN, SECTION_SIM, SECTION_COST, SECTION_RAW, SECTION_ADD}
MEASUREMENT_SECTIONS = {SECTION_STEEL, SECTION_SLAG}
KNOWN_KEYS = {
    SECTION_RUN: {"User Id", "Date", "Status", "Score"},
    SECTION_SIM: {"User Level", "Steel Grade"},
    SECTION_COST: {
        "Time (in minutes)",
        "Tapping mass",
        "Tap temperature",
        "Total Energy",
        "Power",
        "Scrap",
        "Additions",
        "Other consumables",
        "Total Cost",
        "Cost Per Tonne",
    },
}


def parse_event_log(raw_text: Any) -> list[dict]:
    text = str(raw_text or "")
    pattern = re.compile(
        r'["\']?(\d{2}:\d{2}:\d{2})\s*,\s*([^"\'\]\r\n]+)',
        flags=re.I,
    )
    logs = []
    for index, (event_time, event) in enumerate(pattern.findall(text), start=1):
        logs.append(
            normalize_event_log(
                {
                    "log_no": index,
                    "time": event_time,
                    "event": unquote(event.strip().rstrip(",")),
                },
                index,
            )
        )
    return logs


def _renumber_logs(logs: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Return one run's logs with a clean, consecutive ``log_no`` sequence."""

    result = []
    for index, log in enumerate(logs, start=1):
        payload = dict(log)
        payload["log_no"] = index
        result.append(normalize_event_log(payload, index))
    return result


def _split_event_logs(logs: list[dict], expected_count: int) -> list[list[dict]]:
    """Split a combined forwarded-email log into one sequence per result block.

    Steel Challenge result emails end a run with ``Tapping complete``.  Older
    forwarded messages can also be identified by their event time resetting to
    zero.  Refusing an ambiguous split is safer than attaching several runs'
    events to one database record.
    """

    if expected_count < 1:
        raise EmailParseError("결과 블록 수가 올바르지 않습니다.")
    if expected_count == 1:
        return [_renumber_logs(logs)]
    if not logs:
        return [[] for _ in range(expected_count)]

    completed: list[list[dict]] = []
    current: list[dict] = []
    for log in logs:
        current.append(log)
        if "tapping complete" in normalize_text(log.get("event")).casefold():
            completed.append(current)
            current = []
    if current and completed:
        completed[-1].extend(current)
    elif current:
        completed.append(current)
    if len(completed) == expected_count:
        return [_renumber_logs(group) for group in completed]

    reset_groups: list[list[dict]] = []
    current = []
    previous_seconds: Optional[float] = None
    for log in logs:
        seconds = log.get("event_seconds")
        if (
            current
            and previous_seconds is not None
            and seconds is not None
            and float(seconds) < float(previous_seconds)
        ):
            reset_groups.append(current)
            current = []
        current.append(log)
        if seconds is not None:
            previous_seconds = float(seconds)
    if current:
        reset_groups.append(current)
    if len(reset_groups) == expected_count:
        return [_renumber_logs(group) for group in reset_groups]

    raise EmailParseError(
        "여러 Run의 Event Log를 안전하게 분리하지 못했습니다: "
        f"결과 {expected_count}개, Tapping 구간 {len(completed)}개, "
        f"시간 초기화 구간 {len(reset_groups)}개"
    )


def _merge_section_rows(data: dict, section: str, rows: Iterable[Iterable[Any]]) -> None:
    if section in MEASUREMENT_SECTIONS:
        data.update(parse_current_min_max_rows(rows, section))
        return
    parsed = parse_key_value_rows(rows, section)
    for key, value in parsed.items():
        if key not in data:
            data[key] = value
            continue
        suffix = 2
        section_name, item_name = key.split(" > ", 1)
        candidate = f"{section_name} > {item_name}_{suffix}"
        while candidate in data:
            suffix += 1
            candidate = f"{section_name} > {item_name}_{suffix}"
        data[candidate] = value


def _validate_data(data: Mapping[str, Any], process_type: str) -> None:
    required = (
        "Run Information > User Id",
        "Run Information > Date",
        "Simulation Settings > Steel Grade",
    )
    missing = [key for key in required if data.get(key) in (None, "")]
    if missing:
        raise EmailParseError(f"필수 이메일 항목이 없습니다: {', '.join(missing)}")
    required_sections = [SECTION_COST, SECTION_STEEL]
    if process_type == PROCESS_EAF:
        # Some valid EAF results omit the Additions detail table when nothing
        # was added.  Cost Breakdown still carries the additions cost, so the
        # missing detail table must not invalidate an otherwise complete run.
        required_sections.append(SECTION_SLAG)
    missing_sections = [
        section
        for section in required_sections
        if not any(str(key).startswith(f"{section} > ") for key in data)
    ]
    if missing_sections:
        raise EmailParseError(
            f"필수 이메일 섹션이 없습니다: {', '.join(missing_sections)}"
        )
    normalize_datetime(data["Run Information > Date"])
    cost = data.get("Cost Breakdown > Cost Per Tonne")
    score = data.get("Run Information > Score")
    if cost in (None, "") and score in (None, ""):
        raise EmailParseError("Score와 Cost Per Tonne을 찾지 못했습니다.")


def _finalize(
    data: dict,
    raw_text: str,
    process_type: str,
) -> tuple[dict, list[dict]]:
    data["Run Information > Process Type"] = process_type
    data.update(build_structured_sections(data))
    logs = parse_event_log(raw_text)
    _validate_data(data, process_type)
    return data, logs


def parse_html_body(
    html_body: str,
    *,
    process_type: str = PROCESS_EAF,
) -> tuple[dict, list[dict]]:
    if not normalize_text(html_body):
        raise EmailParseError("HTML 본문이 비어 있습니다.")

    soup = BeautifulSoup(html_body, "html.parser")
    data: dict = {}
    current_section: Optional[str] = None
    seen_tables: set[int] = set()

    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "p", "div", "strong", "b", "table"]):
        if node.name == "table":
            node_id = id(node)
            if node_id in seen_tables:
                continue
            seen_tables.add(node_id)
            rows = [
                [normalize_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
                for row in node.find_all("tr")
            ]
            rows = [row for row in rows if any(row)]
            section = _classify_table(rows) or current_section
            if section and section != "Event Log":
                _merge_section_rows(data, section, rows)
            continue

        if node.find_parent("table") is not None:
            continue
        text = normalize_text(node.get_text(" ", strip=True))
        if not text or len(text) > 80:
            continue
        section = canonical_section(text)
        if section:
            current_section = section

    plain_text = soup.get_text("\n", strip=True)
    if not data:
        return parse_plain_body(plain_text, process_type=process_type)
    return _finalize(data, plain_text, process_type)


def parse_html_body_many(
    html_body: str,
    *,
    process_type: str = PROCESS_EAF,
) -> list[tuple[dict, list[dict]]]:
    """Parse every result block embedded in one HTML email."""

    if not normalize_text(html_body):
        raise EmailParseError("HTML 본문이 비어 있습니다.")

    soup = BeautifulSoup(html_body, "html.parser")
    groups: list[list[tuple[str, list[list[str]]]]] = []
    current_group: Optional[list[tuple[str, list[list[str]]]]] = None
    current_section: Optional[str] = None
    seen_tables: set[int] = set()

    for node in soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "p", "div", "strong", "b", "table"]
    ):
        if node.name == "table":
            node_id = id(node)
            if node_id in seen_tables:
                continue
            seen_tables.add(node_id)
            rows = [
                [
                    normalize_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                for row in node.find_all("tr")
            ]
            rows = [row for row in rows if any(row)]
            section = _classify_table(rows) or current_section
            if section == SECTION_RUN:
                if current_group:
                    groups.append(current_group)
                current_group = []
            if current_group is not None and section and section != "Event Log":
                current_group.append((section, rows))
            continue

        if node.find_parent("table") is not None:
            continue
        text = normalize_text(node.get_text(" ", strip=True))
        if not text or len(text) > 80:
            continue
        section = canonical_section(text)
        if section:
            current_section = section

    if current_group:
        groups.append(current_group)
    if not groups:
        plain_text = soup.get_text("\n", strip=True)
        return parse_plain_body_many(plain_text, process_type=process_type)

    parsed_data: list[dict] = []
    for group in groups:
        data: dict = {}
        for section, rows in group:
            _merge_section_rows(data, section, rows)
        data["Run Information > Process Type"] = process_type
        data.update(build_structured_sections(data))
        _validate_data(data, process_type)
        parsed_data.append(data)

    combined_logs = parse_event_log(soup.get_text("\n", strip=True))
    log_groups = _split_event_logs(combined_logs, len(parsed_data))
    return list(zip(parsed_data, log_groups))


def _clean_plain_lines(plain_body: str) -> list[str]:
    lines = []
    for raw_line in plain_body.replace("\xa0", " ").splitlines():
        line = normalize_text(raw_line).strip("\ufeff")
        if line:
            lines.append(line)
    return lines


def parse_plain_body(
    plain_body: str,
    *,
    process_type: str = PROCESS_EAF,
) -> tuple[dict, list[dict]]:
    if not normalize_text(plain_body):
        raise EmailParseError("plain text 본문이 비어 있습니다.")

    lines = _clean_plain_lines(plain_body)
    data: dict = {}
    current_section: Optional[str] = None
    pending_key: Optional[str] = None
    pending_measurement_header: Optional[list[str]] = None

    index = 0
    while index < len(lines):
        line = lines[index]
        section = canonical_section(line)
        if section:
            current_section = section
            pending_key = None
            pending_measurement_header = None
            index += 1
            continue
        if current_section == "Event Log":
            index += 1
            continue

        if "|" in line and current_section:
            row = [normalize_text(part) for part in line.split("|")]
            lowered = [part.casefold() for part in row]
            if lowered and lowered[0] in {"element", "name"}:
                pending_measurement_header = row
            elif current_section in MEASUREMENT_SECTIONS:
                add_measurement(
                    data,
                    current_section,
                    row[0],
                    row[1] if len(row) > 1 else None,
                    row[2] if len(row) > 2 else None,
                    row[3] if len(row) > 3 else None,
                )
            elif current_section in KEY_VALUE_SECTIONS and len(row) >= 2:
                _merge_section_rows(data, current_section, [row])
            index += 1
            continue

        if not current_section:
            index += 1
            continue

        if current_section in MEASUREMENT_SECTIONS and line.casefold() == "element":
            possible_header = [
                item.casefold() for item in lines[index : index + 4]
            ]
            if possible_header[:4] == ["element", "current", "min", "max"]:
                pending_measurement_header = lines[index : index + 4]
                index += 4
                continue

        key = normalize_key(line)
        known_for_section = KNOWN_KEYS.get(current_section, set())
        if key in known_for_section:
            pending_key = key
            index += 1
            continue

        if current_section in {SECTION_RAW, SECTION_ADD}:
            # Alternating Name / Value layout.
            if pending_key is None:
                pending_key = key
            else:
                _merge_section_rows(data, current_section, [[pending_key, line]])
                pending_key = None
            index += 1
            continue

        if pending_key is not None and current_section in KEY_VALUE_SECTIONS:
            _merge_section_rows(data, current_section, [[pending_key, line]])
            pending_key = None
        elif current_section in MEASUREMENT_SECTIONS and pending_measurement_header:
            # Lines without pipes are accepted in groups of four only when a header was seen.
            remaining = lines[index : index + 4]
            if len(remaining) >= 2 and canonical_section(remaining[0]) is None:
                add_measurement(
                    data,
                    current_section,
                    remaining[0],
                    remaining[1],
                    remaining[2] if len(remaining) > 2 else None,
                    remaining[3] if len(remaining) > 3 else None,
                )
                index += min(4, len(remaining))
                continue
        index += 1

    return _finalize(data, plain_body, process_type)


def parse_plain_body_many(
    plain_body: str,
    *,
    process_type: str = PROCESS_EAF,
) -> list[tuple[dict, list[dict]]]:
    """Parse repeated plain-text ``Run Information`` blocks independently."""

    if not normalize_text(plain_body):
        raise EmailParseError("plain text 본문이 비어 있습니다.")
    lines = _clean_plain_lines(plain_body)
    starts = [
        index
        for index, line in enumerate(lines)
        if canonical_section(line) == SECTION_RUN
    ]
    if len(starts) <= 1:
        return [parse_plain_body(plain_body, process_type=process_type)]

    parsed: list[tuple[dict, list[dict]]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end])
        parsed.append(parse_plain_body(chunk, process_type=process_type))
    return parsed


def parse_steel_challenge_email(
    *,
    html_body: Optional[str] = None,
    plain_body: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> tuple[dict, list[dict], dict]:
    """Backward-compatible parser for emails containing exactly one run."""

    parsed = parse_steel_challenge_email_many(
        html_body=html_body,
        plain_body=plain_body,
        metadata=metadata,
    )
    if len(parsed) != 1:
        raise EmailParseError(
            f"이메일에 Run 결과가 {len(parsed)}개 있습니다. "
            "parse_steel_challenge_email_many를 사용해야 합니다."
        )
    return parsed[0]


def parse_steel_challenge_email_many(
    *,
    html_body: Optional[str] = None,
    plain_body: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> list[tuple[dict, list[dict], dict]]:
    """Parse all Steel Challenge runs in an email, HTML first then plain text."""

    errors: list[str] = []
    parsed_metadata = dict(metadata or {})
    process_type = detect_process_type(
        parsed_metadata.get("email_subject") or parsed_metadata.get("subject"),
        html_body,
        plain_body,
    )
    parsed_metadata["process_type"] = process_type

    if html_body:
        try:
            parsed = parse_html_body_many(html_body, process_type=process_type)
            count = len(parsed)
            return [
                (
                    data,
                    logs,
                    {
                        **parsed_metadata,
                        "parsed_status": "success",
                        "parser_source": "html",
                        "parser_error": None,
                        "raw_excerpt": normalize_text(html_body)[:2000],
                        "source_run_index": index,
                        "source_run_count": count,
                    },
                )
                for index, (data, logs) in enumerate(parsed, start=1)
            ]
        except Exception as exc:
            errors.append(f"HTML: {exc}")

    if plain_body:
        try:
            parsed = parse_plain_body_many(plain_body, process_type=process_type)
            count = len(parsed)
            return [
                (
                    data,
                    logs,
                    {
                        **parsed_metadata,
                        "parsed_status": "success",
                        "parser_source": "plain",
                        "parser_error": None,
                        "raw_excerpt": normalize_text(plain_body)[:2000],
                        "source_run_index": index,
                        "source_run_count": count,
                    },
                )
                for index, (data, logs) in enumerate(parsed, start=1)
            ]
        except Exception as exc:
            errors.append(f"plain: {exc}")

    message = "; ".join(errors) or "파싱 가능한 이메일 본문이 없습니다."
    raise EmailParseError(message)
