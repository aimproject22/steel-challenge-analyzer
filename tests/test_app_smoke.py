from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_unauthenticated_app_shows_only_login_gate() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=30)
    assert not app.exception
    markdown = [str(element.value) for element in app.markdown]
    assert any("Steel Challenge 로그인" in value for value in markdown)
    assert not app.dataframe
