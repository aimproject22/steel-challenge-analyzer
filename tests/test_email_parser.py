from __future__ import annotations

from datetime import timedelta

import pytest

from canonical import normalize_datetime
from email_parser import EmailParseError, parse_steel_challenge_email


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


def test_invalid_email_raises() -> None:
    with pytest.raises(EmailParseError):
        parse_steel_challenge_email(plain_body="not a Steel Challenge result")
