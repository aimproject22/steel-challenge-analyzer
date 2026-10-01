from __future__ import annotations

from datetime import timedelta

import pytest

from canonical import normalize_datetime
from email_parser import (
    EmailParseError,
    parse_steel_challenge_email,
    parse_steel_challenge_email_many,
)


def test_plain_email_fixture(sample_email_text: str) -> None:
    data, logs, metadata = parse_steel_challenge_email(plain_body=sample_email_text)

    assert data["Run Information > User Id"] == "lsh05222@yu.ac.kr"
    parsed_date = normalize_datetime(data["Run Information > Date"])
    assert parsed_date.isoformat() == "2026-09-21T22:09:36+09:00"
    assert parsed_date.utcoffset() == timedelta(hours=9)
    assert data["Simulation Settings > Steel Grade"] == "Construction Steel"
    assert data["Run Information > Score"] == pytest.approx(419.22)
    assert data["Cost Breakdown > Time (in minutes)"] == 65
    assert data["Cost Breakdown > Tap temperature"] == 1631
    assert data["Cost Breakdown > Cost Per Tonne"] == pytest.approx(419.22)
    assert data["Additions > Iron Oxide"] == 250
    assert data["Additions > Dolomite"] == 450
    assert data["Additions > Lime"] == 700
    assert data["Steel Composition > C > Current"] == pytest.approx(0.076)
    assert data["Steel Composition > C > Min"] == pytest.approx(0.1)
    assert len(logs) == 14
    assert logs[-1]["time"] == "01:05:48"
    assert logs[-1]["event_seconds"] == pytest.approx(3948)
    assert logs[-1]["event"] == "Tapping complete"
    assert metadata["parser_source"] == "plain"


def test_html_email_tables(sample_email_html: str) -> None:
    data, logs, metadata = parse_steel_challenge_email(html_body=sample_email_html)

    assert data["Run Information > User Id"] == "lsh05222@yu.ac.kr"
    assert data["Cost Breakdown > Total Energy"] == 36069
    assert data["Cost Breakdown > Total Energy_2"] == 411
    assert data["Additions > Iron Oxide"] == 250
    assert data["Slag Composition > Basicity > Max"] == pytest.approx(2.5)
    assert logs[-1]["event_seconds"] == pytest.approx(3948)
    assert metadata["parser_source"] == "html"


def test_html_failure_falls_back_to_plain(sample_email_text: str) -> None:
    data, logs, metadata = parse_steel_challenge_email(
        html_body="<html><body>not a result</body></html>",
        plain_body=sample_email_text,
    )
    assert data["Run Information > User Id"] == "lsh05222@yu.ac.kr"
    assert len(logs) == 14
    assert metadata["parser_source"] == "plain"


def test_secondary_steelmaking_does_not_require_eaf_only_sections(
    sample_email_html: str,
) -> None:
    html = sample_email_html
    html = html.replace(
        """<h2>Additions</h2>
      <table><tr><td>Iron Oxide</td><td>250</td></tr>
      <tr><td>Dolomite</td><td>450</td></tr><tr><td>Lime</td><td>700</td></tr></table>""",
        "",
    )
    html = html.replace(
        """<h2>Slag Composition</h2>
      <table><tr><th>Element</th><th>Current</th><th>Min</th><th>Max</th></tr>
      <tr><td>Basicity</td><td>0.852</td><td>1.5</td><td>2.5</td></tr></table>""",
        "",
    )

    data, logs, metadata = parse_steel_challenge_email(
        html_body=html,
        metadata={"email_subject": "Fwd: Secondary Steelmaking"},
    )

    assert data["Run Information > Process Type"] == "Secondary Steelmaking"
    assert data["Cost Breakdown > Cost Per Tonne"] == pytest.approx(419.22)
    assert not any(key.startswith("Additions > ") for key in data)
    assert not any(key.startswith("Slag Composition > ") for key in data)
    assert logs[-1]["event"] == "Tapping complete"
    assert metadata["process_type"] == "Secondary Steelmaking"


def test_eaf_without_additions_detail_table_is_still_valid(
    sample_email_html: str,
) -> None:
    html = sample_email_html.replace(
        """<h2>Additions</h2>
      <table><tr><td>Iron Oxide</td><td>250</td></tr>
      <tr><td>Dolomite</td><td>450</td></tr><tr><td>Lime</td><td>700</td></tr></table>""",
        "",
    )

    data, logs, metadata = parse_steel_challenge_email(html_body=html)

    assert data["Run Information > Process Type"] == "Electric Arc Furnace"
    assert data["Cost Breakdown > Cost Per Tonne"] == pytest.approx(419.22)
    assert not any(key.startswith("Additions > ") for key in data)
    assert logs[-1]["event"] == "Tapping complete"
    assert metadata["parser_source"] == "html"


def test_invalid_email_raises() -> None:
    with pytest.raises(EmailParseError):
        parse_steel_challenge_email(plain_body="not a Steel Challenge result")


def test_multiple_html_results_are_split_with_their_event_logs(
    sample_email_html: str,
) -> None:
    second = (
        sample_email_html
        .replace("lsh05222@yu.ac.kr", "second@yu.ac.kr")
        .replace("21/09/2026 22:09:36", "22/09/2026 08:10:11")
        .replace("$ 419.22", "$ 401.11")
        .replace("01:05:48", "00:55:00")
    )
    combined = sample_email_html.replace("</body></html>", "") + second.replace(
        "<html><body>", ""
    )

    parsed = parse_steel_challenge_email_many(html_body=combined)

    assert len(parsed) == 2
    assert parsed[0][0]["Run Information > User Id"] == "lsh05222@yu.ac.kr"
    assert parsed[1][0]["Run Information > User Id"] == "second@yu.ac.kr"
    assert parsed[0][1][-1]["event_seconds"] == pytest.approx(3948)
    assert parsed[1][1][-1]["event_seconds"] == pytest.approx(3300)
    assert [log["log_no"] for log in parsed[1][1]] == [1, 2]
    assert parsed[0][2]["source_run_index"] == 1
    assert parsed[1][2]["source_run_index"] == 2
    assert all(item[2]["source_run_count"] == 2 for item in parsed)


def test_multiple_plain_results_are_split(sample_email_text: str) -> None:
    second = (
        sample_email_text
        .replace("lsh05222@yu.ac.kr", "plain-second@yu.ac.kr")
        .replace("21/09/2026 22:09:36", "23/09/2026 09:00:00")
    )

    parsed = parse_steel_challenge_email_many(
        plain_body=f"{sample_email_text}\n{second}"
    )

    assert len(parsed) == 2
    assert parsed[1][0]["Run Information > User Id"] == "plain-second@yu.ac.kr"
    assert len(parsed[0][1]) == 14
    assert len(parsed[1][1]) == 14


def test_single_result_api_rejects_composite_email(sample_email_html: str) -> None:
    combined = sample_email_html.replace("</body></html>", "") + sample_email_html.replace(
        "<html><body>", ""
    )
    with pytest.raises(EmailParseError, match="2개"):
        parse_steel_challenge_email(html_body=combined)


def test_three_html_results_produce_three_independent_runs(
    sample_email_html: str,
) -> None:
    fragments = []
    for index in range(3):
        fragment = (
            sample_email_html
            .replace("lsh05222@yu.ac.kr", f"student-{index}@yu.ac.kr")
            .replace("21/09/2026 22:09:36", f"2{index + 1}/09/2026 22:09:36")
        )
        if index:
            fragment = fragment.replace("<html><body>", "")
        if index < 2:
            fragment = fragment.replace("</body></html>", "")
        fragments.append(fragment)

    parsed = parse_steel_challenge_email_many(html_body="".join(fragments))

    assert len(parsed) == 3
    assert [item[2]["source_run_index"] for item in parsed] == [1, 2, 3]
    assert [item[0]["Run Information > User Id"] for item in parsed] == [
        "student-0@yu.ac.kr",
        "student-1@yu.ac.kr",
        "student-2@yu.ac.kr",
    ]
