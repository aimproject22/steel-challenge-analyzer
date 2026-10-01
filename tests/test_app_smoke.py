from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui_components import page_names


def test_unauthenticated_app_shows_only_login_gate() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=30)
    assert not app.exception
    markdown = [str(element.value) for element in app.markdown]
    assert any("Steel Challenge 로그인" in value for value in markdown)
    assert not app.dataframe


def test_public_navigation_is_read_only_but_downloadable() -> None:
    pages = page_names(
        {
            "public_access": True,
            "can_download": True,
            "is_admin": False,
        }
    )

    assert "다운로드" in pages
    assert "수동 업로드" not in pages
    assert "데이터 품질" not in pages
    assert "사용자 관리" not in pages
