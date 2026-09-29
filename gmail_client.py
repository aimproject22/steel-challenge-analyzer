"""Small Gmail API adapter used by the scheduled ingestion worker."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import get_secret


GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
PROCESSED_LABEL = "SteelChallengeProcessed"


def build_gmail_service():
    credentials = Credentials(
        token=None,
        refresh_token=get_secret("GMAIL_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=get_secret("GMAIL_CLIENT_ID"),
        client_secret=get_secret("GMAIL_CLIENT_SECRET"),
        scopes=[GMAIL_MODIFY_SCOPE],
    )
    credentials.refresh(Request())
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def list_messages(
    service: Any,
    *,
    target_address: str,
    max_results: int = 100,
    processed_label: str = PROCESSED_LABEL,
) -> list[dict]:
    query = f"to:{target_address} -label:{processed_label}"
    messages: list[dict] = []
    page_token: Optional[str] = None
    while len(messages) < max_results:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(500, max_results - len(messages)),
                pageToken=page_token,
            )
            .execute()
        )
        messages.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return messages[:max_results]


def get_message(service: Any, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def _decode_base64url(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    for encoding in ("utf-8", "cp949", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def decode_mime_body(
    message: dict,
    *,
    service: Any = None,
) -> tuple[str, str]:
    """Return ``(plain_text, html)`` while walking nested multipart payloads."""

    message_id = message.get("id")
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = str(part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        data = body.get("data")
        if not data and body.get("attachmentId") and service is not None and message_id:
            attachment = (
                service.users()
                .messages()
                .attachments()
                .get(
                    userId="me",
                    messageId=message_id,
                    id=body["attachmentId"],
                )
                .execute()
            )
            data = attachment.get("data")
        if data and mime_type in {"text/plain", "text/html"}:
            decoded = _decode_base64url(data)
            (html_parts if mime_type == "text/html" else plain_parts).append(decoded)
        for child in part.get("parts") or []:
            walk(child)

    walk(message.get("payload") or {})
    return "\n".join(plain_parts), "\n".join(html_parts)


def extract_message_metadata(message: dict) -> dict:
    headers = {
        str(item.get("name") or "").casefold(): str(item.get("value") or "")
        for item in (message.get("payload") or {}).get("headers", [])
    }
    sender_name, sender_email = parseaddr(headers.get("from", ""))

    received_at: Optional[datetime] = None
    internal_date = message.get("internalDate")
    if internal_date:
        try:
            received_at = datetime.fromtimestamp(
                int(internal_date) / 1000,
                tz=timezone.utc,
            )
        except (TypeError, ValueError, OSError):
            received_at = None
    if received_at is None and headers.get("date"):
        try:
            received_at = parsedate_to_datetime(headers["date"])
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            received_at = None

    return {
        "gmail_message_id": message.get("id"),
        "gmail_thread_id": message.get("threadId"),
        "sender_name": sender_name or None,
        "sender_email": sender_email or None,
        "email_subject": headers.get("subject") or "",
        "email_received_at": received_at.isoformat() if received_at else None,
    }


def _get_or_create_label_id(service: Any, label_name: str) -> str:
    response = service.users().labels().list(userId="me").execute()
    for label in response.get("labels", []):
        if str(label.get("name")).casefold() == label_name.casefold():
            return str(label["id"])
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return str(created["id"])


def add_processed_label(
    service: Any,
    message_id: str,
    *,
    label_name: str = PROCESSED_LABEL,
) -> None:
    label_id = _get_or_create_label_id(service, label_name)
    (
        service.users()
        .messages()
        .modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id], "removeLabelIds": []},
        )
        .execute()
    )

