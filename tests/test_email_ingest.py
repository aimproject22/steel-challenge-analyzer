from __future__ import annotations

from typing import Any

import email_ingest


def test_forced_reprocess_replaces_one_email_with_multiple_runs(
    monkeypatch,
    sample_email_html: str,
) -> None:
    second = (
        sample_email_html
        .replace("lsh05222@yu.ac.kr", "second@yu.ac.kr")
        .replace("21/09/2026 22:09:36", "22/09/2026 08:10:11")
    )
    combined = sample_email_html.replace("</body></html>", "") + second.replace(
        "<html><body>", ""
    )
    database = object()
    gmail = object()
    email_row = {
        "id": 77,
        "gmail_message_id": "gmail-composite",
        "parsed_status": "success",
    }
    captured: dict[str, Any] = {}

    monkeypatch.setattr(email_ingest, "create_service_client", lambda: database)
    monkeypatch.setattr(email_ingest, "build_gmail_service", lambda: gmail)
    monkeypatch.setattr(email_ingest, "get_secret", lambda key: "target@gmail.com")
    monkeypatch.setattr(
        email_ingest,
        "get_email_messages_for_run_ids",
        lambda client, run_ids: [email_row],
    )
    monkeypatch.setattr(
        email_ingest,
        "get_message",
        lambda service, message_id: {"id": message_id},
    )
    monkeypatch.setattr(
        email_ingest,
        "extract_message_metadata",
        lambda message: {
            "gmail_message_id": "gmail-composite",
            "sender_name": "Steel University",
            "sender_email": "simulators@steeluniversity.org",
            "email_subject": "Fwd: Electric Arc Furnace simulation",
        },
    )
    monkeypatch.setattr(
        email_ingest,
        "decode_mime_body",
        lambda message, service: ("", combined),
    )

    def replace(client, email_message_id, entries):
        captured["email_message_id"] = email_message_id
        captured["entries"] = entries
        return [501, 502]

    monkeypatch.setattr(email_ingest, "replace_email_runs_with_logs", replace)
    monkeypatch.setattr(email_ingest, "update_email_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(email_ingest, "add_processed_label", lambda *args, **kwargs: None)

    summary = email_ingest.ingest_messages(reprocess_run_ids=[1035])

    assert summary == {
        "found": 1,
        "saved": 2,
        "saved_messages": 1,
        "failed": 0,
        "duplicates": 0,
    }
    assert captured["email_message_id"] == 77
    entries = captured["entries"]
    assert [entry["run_payload"]["source_run_index"] for entry in entries] == [1, 2]
    assert all(entry["run_payload"]["email_message_id"] == 77 for entry in entries)
    assert [len(entry["log_payload"]) for entry in entries] == [2, 2]
