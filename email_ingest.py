"""Scheduled Gmail -> parser -> Supabase ingestion entry point."""

from __future__ import annotations

import argparse
import logging
import re
from typing import Any, Optional, Sequence

from auth_utils import create_service_client
from config import get_secret
from db_utils import (
    build_run_payload,
    create_email_message,
    get_email_message_by_gmail_id,
    get_email_messages_for_run_ids,
    increment_duplicate_skip,
    replace_email_runs_with_logs,
    update_email_message,
)
from email_parser import parse_steel_challenge_email_many
from gmail_client import (
    PROCESSED_LABEL,
    add_processed_label,
    build_gmail_service,
    decode_mime_body,
    extract_message_metadata,
    get_message,
    list_messages,
)


LOGGER = logging.getLogger("steel_challenge_ingest")


def ingest_messages(
    *,
    max_results: int = 100,
    retry_failed: bool = False,
    reprocess_run_ids: Optional[Sequence[int]] = None,
) -> dict:
    gmail = build_gmail_service()
    database = create_service_client()
    target_address = str(get_secret("GMAIL_TARGET_ADDRESS"))
    summaries = {
        "found": 0,
        "saved": 0,
        "saved_messages": 0,
        "failed": 0,
        "duplicates": 0,
    }

    forced_message_ids: set[str] = set()
    if reprocess_run_ids:
        stored_messages = get_email_messages_for_run_ids(database, reprocess_run_ids)
        messages = []
        for row in stored_messages:
            gmail_message_id = str(row.get("gmail_message_id") or "")
            if gmail_message_id:
                forced_message_ids.add(gmail_message_id)
                messages.append({"id": gmail_message_id, "_email_row": row})
    else:
        messages = list_messages(
            gmail,
            target_address=target_address,
            max_results=max_results,
            processed_label=PROCESSED_LABEL,
        )
    summaries["found"] = len(messages)

    for listing in messages:
        gmail_message_id = str(listing.get("id") or "")
        if not gmail_message_id:
            continue
        email_row: dict[str, Any] | None = None
        try:
            existing = listing.get("_email_row") or get_email_message_by_gmail_id(
                database, gmail_message_id
            )
            if existing and not (
                gmail_message_id in forced_message_ids
                or (retry_failed and existing.get("parsed_status") == "failed")
            ):
                increment_duplicate_skip(database, existing)
                summaries["duplicates"] += 1
                if existing.get("parsed_status") == "success":
                    try:
                        add_processed_label(gmail, gmail_message_id)
                    except Exception as label_error:
                        LOGGER.warning(
                            "Duplicate message %s still could not be labeled: %s",
                            gmail_message_id,
                            type(label_error).__name__,
                        )
                continue

            message = get_message(gmail, gmail_message_id)
            metadata = extract_message_metadata(message)
            plain_body, html_body = decode_mime_body(message, service=gmail)
            raw_excerpt = (html_body or plain_body or "")[:4000]

            if existing:
                email_row = existing
                update_email_message(
                    database,
                    int(email_row["id"]),
                    parsed_status="processing",
                    parser_error=None,
                    raw_excerpt=raw_excerpt,
                )
            else:
                metadata.update(
                    {
                        "parsed_status": "processing",
                        "raw_excerpt": raw_excerpt,
                    }
                )
                email_row = create_email_message(database, metadata)

            parsed_runs = parse_steel_challenge_email_many(
                html_body=html_body,
                plain_body=plain_body,
                metadata=metadata,
            )
            sender_name = str(metadata.get("sender_name") or "")
            sender_email = str(metadata.get("sender_email") or "")
            display_sender = sender_name or sender_email or "Gmail sender"
            base_file_name = str(
                metadata.get("email_subject") or f"gmail-{gmail_message_id}"
            )
            entries = []
            for data, logs, parse_metadata in parsed_runs:
                source_run_index = int(parse_metadata["source_run_index"])
                source_run_count = int(parse_metadata["source_run_count"])
                file_name = base_file_name
                if source_run_count > 1:
                    file_name = (
                        f"{base_file_name} [Run {source_run_index}/{source_run_count}]"
                    )
                run_payload, log_payload = build_run_payload(
                    uploader=display_sender,
                    file_name=file_name,
                    data=data,
                    logs=logs,
                    source="gmail",
                    sender_name=sender_name or None,
                    sender_email=sender_email or None,
                    email_message_id=int(email_row["id"]),
                    source_run_index=source_run_index,
                )
                entries.append(
                    {"run_payload": run_payload, "log_payload": log_payload}
                )

            saved_ids = replace_email_runs_with_logs(
                database,
                int(email_row["id"]),
                entries,
            )
            first_metadata = parsed_runs[0][2]
            update_email_message(
                database,
                int(email_row["id"]),
                parsed_status="success",
                parser_source=first_metadata.get("parser_source"),
                parser_error=None,
                raw_excerpt=first_metadata.get("raw_excerpt"),
            )
            summaries["saved"] += len(saved_ids)
            summaries["saved_messages"] += 1

            try:
                add_processed_label(gmail, gmail_message_id)
            except Exception as label_error:  # label failure must not undo ingestion
                LOGGER.warning(
                    "Message %s saved but label update failed: %s",
                    gmail_message_id,
                    type(label_error).__name__,
                )
        except Exception as exc:
            summaries["failed"] += 1
            error_text = f"{type(exc).__name__}: {exc}"[:4000]
            LOGGER.error("Message %s failed: %s", gmail_message_id, error_text)
            if email_row:
                try:
                    update_email_message(
                        database,
                        int(email_row["id"]),
                        parsed_status="failed",
                        parser_error=error_text,
                    )
                except Exception:
                    LOGGER.exception("Could not record parser failure for %s", gmail_message_id)
            # Continue processing the remaining messages.
            continue

    return summaries


def _parse_run_ids(raw_value: str) -> list[int]:
    values = [item for item in re.split(r"[\s,]+", raw_value.strip()) if item]
    run_ids = sorted({int(item) for item in values})
    if any(value < 1 for value in run_ids):
        raise argparse.ArgumentTypeError("Run ID는 1 이상의 정수여야 합니다.")
    return run_ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Steel Challenge result emails")
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--reprocess-run-ids",
        type=_parse_run_ids,
        default=[],
        metavar="ID[,ID...]",
        help="기존 Run ID의 원본 Gmail 메시지를 다시 분리·저장합니다.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = ingest_messages(
        max_results=max(1, min(args.max_results, 500)),
        retry_failed=args.retry_failed,
        reprocess_run_ids=args.reprocess_run_ids,
    )
    LOGGER.info(
        "Ingestion complete: found_messages=%d saved_runs=%d "
        "saved_messages=%d failed_messages=%d duplicates=%d",
        summary["found"],
        summary["saved"],
        summary["saved_messages"],
        summary["failed"],
        summary["duplicates"],
    )
    # Per-message failures are recorded in Supabase and must not abort the
    # scheduled job; configuration/Gmail/database bootstrap failures still
    # raise before this point and correctly fail the workflow.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
