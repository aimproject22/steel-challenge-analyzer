from __future__ import annotations

from pathlib import Path

import pytest

from parser import parse_docx


DOCX_DIRECTORY = Path(__file__).resolve().parents[1] / "docx_results"


@pytest.mark.parametrize("path", sorted(DOCX_DIRECTORY.glob("*.docx")))
def test_existing_docx_regression(path: Path) -> None:
    data, logs = parse_docx(path)
    assert data["Run Information > User Id"]
    assert data["Run Information > Date"]
    assert data["Simulation Settings > Steel Grade"]
    assert data["Cost Breakdown > Cost Per Tonne"] is not None
    assert "Steel Composition > C > Current" in data
    assert "Steel Composition > C > Min" in data
    assert "Steel Composition > C > Max" in data
    assert logs
    assert all(log["log_no"] == index for index, log in enumerate(logs, start=1))
