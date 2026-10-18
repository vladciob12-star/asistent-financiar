"""Opt-in Gmail polling for Booking CSV statements.

Only read-only Gmail API calls are used. Message bodies, headers, and raw CSV
attachments are never persisted; the existing Booking parser stores financial
fields and hashes reservation references before inserting them.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from threading import Event, Thread

from booking_imports import import_booking_csv
from finance_store import connect, import_booking_records

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_API_RESPONSE_BYTES = 14 * 1024 * 1024
POLL_INTERVAL_SECONDS = 15 * 60
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class GmailBookingConfig:
    client_id: str
    client_secret: str
    refresh_token: str

    @classmethod
    def from_env(cls) -> "GmailBookingConfig | None":
        values = (os.environ.get("GMAIL_CLIENT_ID", "").strip(),
                  os.environ.get("GMAIL_CLIENT_SECRET", "").strip(),
                  os.environ.get("GMAIL_REFRESH_TOKEN", "").strip())
        if not any(values):
            return None
        if not all(values):
            raise ValueError("Gmail OAuth credentials are incomplete.")
        return cls(*values)


def _json_request(url: str, *, headers: dict[str, str] | None = None,
                  data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(MAX_API_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("External API request failed.") from exc
    if len(payload) > MAX_API_RESPONSE_BYTES:
        raise RuntimeError("External API response exceeded size limit.")
    try:
        result = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("External API returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("External API returned an unexpected response.")
    return result


def _access_token(config: GmailBookingConfig) -> str:
    data = urllib.parse.urlencode({
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": config.refresh_token,
        "grant_type": "refresh_token",
    }).encode("ascii")
    token = _json_request(TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)
    value = token.get("access_token")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Google OAuth token refresh failed.")
    return value


def _api_json(url: str, token: str) -> dict:
    return _json_request(url, headers={"Authorization": f"Bearer {token}"})


def _booking_sender(metadata: dict) -> bool:
    headers = metadata.get("payload", {}).get("headers", [])
    for header in headers if isinstance(headers, list) else []:
        if isinstance(header, dict) and str(header.get("name", "")).casefold() == "from":
            address = parseaddr(str(header.get("value", "")))[1].casefold()
            domain = address.rsplit("@", 1)[-1] if "@" in address else ""
            return domain == "booking.com" or domain.endswith(".booking.com")
    return False


def _parts(payload: dict):
    for part in payload.get("parts", []) if isinstance(payload.get("parts", []), list) else []:
        if not isinstance(part, dict):
            continue
        yield part
        yield from _parts(part)


def _attachment(part: dict, message_id: str, token: str) -> bytes | None:
    body = part.get("body") if isinstance(part.get("body"), dict) else {}
    size = body.get("size")
    if not isinstance(size, int) or size < 0 or size > MAX_ATTACHMENT_BYTES:
        return None
    encoded = body.get("data")
    if not encoded and body.get("attachmentId"):
        attachment_id = urllib.parse.quote(str(body["attachmentId"]), safe="")
        msg = urllib.parse.quote(message_id, safe="")
        response = _api_json(f"{GMAIL_API}/messages/{msg}/attachments/{attachment_id}", token)
        encoded = response.get("data")
    if not isinstance(encoded, str):
        return None
    try:
        content = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, binascii.Error):
        return None
    return content if len(content) <= MAX_ATTACHMENT_BYTES else None


def _already_processed(db_path: Path, message_id: str) -> bool:
    with closing(connect(db_path)) as db:
        return db.execute("SELECT 1 FROM gmail_booking_messages WHERE message_id=?", (message_id,)).fetchone() is not None


def _mark_processed(db_path: Path, message_id: str) -> None:
    with closing(connect(db_path)) as db:
        with db:
            db.execute("INSERT OR IGNORE INTO gmail_booking_messages(message_id) VALUES(?)", (message_id,))


def sync_booking_mail(db_path: Path, config: GmailBookingConfig) -> dict[str, int]:
    """Import new Booking CSV attachments; safe to retry after any interruption."""
    token = _access_token(config)
    query = urllib.parse.urlencode({
        "q": "from:booking.com has:attachment filename:csv newer_than:90d",
        "maxResults": "100",
        "fields": "messages(id),nextPageToken",
    })
    result = _api_json(f"{GMAIL_API}/messages?{query}", token)
    messages = result.get("messages", [])
    if not isinstance(messages, list):
        raise RuntimeError("Gmail returned an unexpected message list.")
    messages = list(messages)
    # Gmail caps a page at 100. Walk a bounded number of pages so an inbox
    # with many statements is not permanently stuck on its newest page.
    for _ in range(9):
        page_token = result.get("nextPageToken")
        if not isinstance(page_token, str) or not page_token:
            break
        page_query = urllib.parse.urlencode({
            "q": "from:booking.com has:attachment filename:csv newer_than:90d",
            "maxResults": "100", "pageToken": page_token, "fields": "messages(id),nextPageToken",
        })
        result = _api_json(f"{GMAIL_API}/messages?{page_query}", token)
        page_messages = result.get("messages", [])
        if not isinstance(page_messages, list):
            raise RuntimeError("Gmail returned an unexpected message list.")
        messages.extend(page_messages)
    counts = {"messages_scanned": 0, "attachments_imported": 0, "records_imported": 0}
    for item in messages[:1000]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        message_id = item["id"]
        if _already_processed(db_path, message_id):
            continue
        encoded_id = urllib.parse.quote(message_id, safe="")
        metadata = _api_json(
            f"{GMAIL_API}/messages/{encoded_id}?format=metadata&metadataHeaders=From&fields=payload(headers(name,value))",
            token,
        )
        counts["messages_scanned"] += 1
        if _booking_sender(metadata):
            message = _api_json(
                f"{GMAIL_API}/messages/{encoded_id}?format=full&fields=payload(parts(filename,mimeType,body(attachmentId,data,size),parts(filename,mimeType,body(attachmentId,data,size),parts(filename,mimeType,body(attachmentId,data,size),parts(filename,mimeType,body(attachmentId,data,size))))))",
                token,
            )
            for part in _parts(message.get("payload", {})):
                name = str(part.get("filename", ""))
                if not name.casefold().endswith(".csv"):
                    continue
                content = _attachment(part, message_id, token)
                if not content:
                    continue
                temp_name = None
                try:
                    with tempfile.NamedTemporaryFile(prefix="booking-mail-", suffix=".csv", delete=False) as temp:
                        temp.write(content)
                        temp_name = Path(temp.name)
                    records = import_booking_csv(temp_name)
                    with closing(connect(db_path)) as db:
                        _, inserted = import_booking_records(db, "booking-email-report.csv", content, records)
                    counts["attachments_imported"] += 1
                    counts["records_imported"] += inserted
                except (ValueError, OSError):
                    # Invalid/unrelated CSVs are not logged or stored. Continue
                    # scanning other attachments in the same Booking message.
                    continue
                finally:
                    if temp_name is not None:
                        temp_name.unlink(missing_ok=True)
        _mark_processed(db_path, message_id)
    return counts


def _poll_forever(db_path: Path, config: GmailBookingConfig, stop: Event) -> None:
    while not stop.is_set():
        try:
            counts = sync_booking_mail(db_path, config)
            if counts["attachments_imported"]:
                print(f"Booking Gmail sync imported {counts['records_imported']} new financial rows.")
        except Exception:
            # Never write credentials, message content, attachment names or
            # financial values to application logs.
            print("Booking Gmail sync failed; check OAuth configuration and Gmail API access.")
        stop.wait(POLL_INTERVAL_SECONDS)


def start_booking_mail_poller(db_path: Path) -> tuple[Thread | None, Event | None]:
    if os.environ.get("GMAIL_BOOKING_AUTO_IMPORT", "").casefold() != "true":
        return None, None
    config = GmailBookingConfig.from_env()
    if config is None:
        raise ValueError("Gmail auto import requires configured OAuth credentials.")
    stop = Event()
    thread = Thread(target=_poll_forever, args=(db_path, config, stop), name="booking-gmail-import", daemon=True)
    thread.start()
    print("Booking Gmail importer enabled (read-only, filtered to Booking CSV reports).")
    return thread, stop

