"""Opt-in Meta WhatsApp Cloud API helpers; never connects until configured."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable

from finance_store import monthly_summary


MAX_WEBHOOK_BYTES = 1_000_000
MAX_META_RESPONSE_BYTES = 64_000
GRAPH_VERSION_RE = re.compile(r"v\d{2,3}\.\d")
MONTH_RE = re.compile(r"(?<!\d)(20\d{2}-(?:0[1-9]|1[0-2]))(?!\d)")


@dataclass(frozen=True)
class WhatsAppConfig:
    verify_token: str
    app_secret: str
    access_token: str
    phone_number_id: str
    api_version: str
    allowed_senders: frozenset[str]
    enable_messaging: bool
    allow_reports: bool

    @classmethod
    def from_env(cls) -> "WhatsAppConfig":
        version = os.environ.get("WHATSAPP_API_VERSION", "")
        return cls(
            verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
            app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
            access_token=os.environ.get("WHATSAPP_ACCESS_TOKEN", ""),
            phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            api_version=version,
            allowed_senders=frozenset(
                value.strip() for value in os.environ.get("WHATSAPP_ALLOWED_SENDERS", "").split(",")
                if value.strip()
            ),
            enable_messaging=os.environ.get("WHATSAPP_ENABLE_MESSAGING", "").casefold() == "true",
            allow_reports=os.environ.get("WHATSAPP_ALLOW_REPORTS", "").casefold() == "true",
        )

    @property
    def webhook_ready(self) -> bool:
        return bool(self.enable_messaging and self.verify_token and self.app_secret and self.allowed_senders)

    @property
    def messaging_ready(self) -> bool:
        return bool(self.webhook_ready and self.access_token and self.phone_number_id
                    and GRAPH_VERSION_RE.fullmatch(self.api_version))


class MetaAPIError(RuntimeError):
    """Sanitized error; never includes response bodies or credentials."""


class MetaCloudAPI:
    def __init__(self, config: WhatsAppConfig):
        if not config.messaging_ready:
            raise ValueError("Configurația de mesagerie WhatsApp este incompletă.")
        self.config = config
        self.base_url = f"https://graph.facebook.com/{config.api_version}/{config.phone_number_id}"

    def _post(self, path: str, body: bytes, content_type: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/{path}", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.config.access_token}",
                     "Content-Type": content_type, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = response.read(MAX_META_RESPONSE_BYTES + 1)
                if len(raw) > MAX_META_RESPONSE_BYTES:
                    raise MetaAPIError("Răspuns Meta prea mare.")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MetaAPIError("Conexiunea Meta a eșuat.") from exc
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetaAPIError("Răspuns Meta invalid.") from exc
        if not isinstance(parsed, dict):
            raise MetaAPIError("Răspuns Meta invalid.")
        return parsed

    def send_text(self, recipient: str, text: str) -> None:
        payload = {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": recipient, "type": "text", "text": {"preview_url": False, "body": text},
        }
        response = self._post("messages", json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              "application/json")
        if not response.get("messages"):
            raise MetaAPIError("Meta nu a confirmat livrarea mesajului.")

    def send_pdf(self, recipient: str, path: Path, caption: str) -> None:
        boundary = "----codex" + uuid.uuid4().hex
        pdf = Path(path).read_bytes()
        filename = Path(path).name
        parts = [
            ("messaging_product", b"whatsapp", None),
            ("type", b"application/pdf", None),
            ("file", pdf, filename),
        ]
        chunks: list[bytes] = []
        for name, value, file_name in parts:
            chunks.append(f"--{boundary}\r\n".encode())
            if file_name:
                chunks.append((f'Content-Disposition: form-data; name="{name}"; filename="{file_name}"\r\n'
                                "Content-Type: application/pdf\r\n\r\n").encode())
            else:
                chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            chunks.extend([value, b"\r\n"])
        chunks.append(f"--{boundary}--\r\n".encode())
        uploaded = self._post("media", b"".join(chunks), f"multipart/form-data; boundary={boundary}")
        media_id = uploaded.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise MetaAPIError("Meta nu a confirmat încărcarea PDF-ului.")
        payload = {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": recipient, "type": "document",
            "document": {"id": media_id, "filename": filename, "caption": caption},
        }
        sent = self._post("messages", json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          "application/json")
        if not sent.get("messages"):
            raise MetaAPIError("Meta nu a confirmat trimiterea documentului.")


def verify_signature(app_secret: str, body: bytes, signature: str) -> bool:
    if not app_secret or not re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature or ""):
        return False
    import hashlib
    import hmac
    expected = "sha256=" + hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _month_from_text(text: str, now: int) -> str:
    match = MONTH_RE.search(text)
    if match:
        return match.group(1)
    return date.fromtimestamp(now).strftime("%Y-%m")


def financial_answer(db, text: str, now: int) -> tuple[str, str | None]:
    """Return a safe aggregate answer and optional PDF month; no free-form SQL/LLM."""
    normalized = " ".join(text.casefold().split())
    month = _month_from_text(normalized, now)
    if normalized in {"/ajutor", "ajutor", "help", "/help"}:
        return ("Poți întreba: venituri, cheltuieli, profit estimat sau bani disponibili. "
                "Pentru situația agregată scrie /situatie; pentru raport PDF: /raport AAAA-LL. "
                "Sunt folosite numai totaluri agregate.", None)
    if normalized.startswith("/raport") or "raport pdf" in normalized:
        if re.search(r"(?:^|\s)/raport(?:\s|$)", normalized) or "raport pdf" in normalized:
            return (f"Pregătesc raportul financiar agregat pentru {month}.", month)
    report = monthly_summary(db, month)
    def sum_key(key: str) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for currency_rows in report["activities"].values():
            for currency, values in currency_rows.items():
                result[currency] = result.get(currency, Decimal(0)) + Decimal(values[key])
        return result
    incomes = sum_key("income")
    expenses = sum_key("expense")
    commissions = sum_key("commission")
    profits = {currency: incomes.get(currency, Decimal(0)) - expenses.get(currency, Decimal(0))
               - commissions.get(currency, Decimal(0)) for currency in incomes.keys() | expenses.keys() | commissions.keys()}
    if normalized.startswith("/situatie") or "situație financiară" in normalized or "situatie financiara" in normalized:
        def compact(values: dict[str, Decimal]) -> str:
            return ", ".join(f"{amount:.2f} {currency}" for currency, amount in sorted(values.items())) or "—"
        balances = report["bank_available_balances"]
        available = "; ".join(f"{row['amount']} {row['currency']} (la {row['as_of']})" for row in balances)
        return (f"Situație {month} — venituri: {compact(incomes)}; cheltuieli și comisioane: "
                f"{compact({currency: expenses.get(currency, Decimal(0)) + commissions.get(currency, Decimal(0)) for currency in expenses.keys() | commissions.keys()})}; "
                f"profit estimat: {compact(profits)}; bani disponibili: {available or 'soldul trebuie introdus manual din extras'}. "
                "Valori agregate estimative, fără conversie valutară.", None)
    if any(word in normalized for word in ("disponibil", "sold bancar", "soldul", "bani am")):
        rows = report["bank_available_balances"]
        if not rows:
            answer = "Nu există sold bancar disponibil. Acesta apare doar după ce introduci manual soldul din extras în aplicație."
        else:
            answer = "Sold disponibil introdus manual: " + "; ".join(
                f"{row['amount']} {row['currency']} (la {row['as_of']})" for row in rows)
        return answer, None
    if any(word in normalized for word in ("cheltu", "cost", "comision")):
        totals = {currency: expenses.get(currency, Decimal(0)) + commissions.get(currency, Decimal(0))
                  for currency in expenses.keys() | commissions.keys()}
        title, values = "Cheltuieli și comisioane", totals
    elif any(word in normalized for word in ("profit", "câștig", "castig", "rămas", "ramas")):
        title, values = "Profit estimat", profits
    elif any(word in normalized for word in ("venit", "încas", "incas")):
        title, values = "Venituri clasificate", incomes
    else:
        return ("Nu am identificat întrebarea. Scrie /ajutor pentru comenzile acceptate. "
                "Nu trimit extrase, descrieri individuale sau clasificări incerte pe WhatsApp.", None)
    formatted = "; ".join(f"{amount:.2f} {currency}" for currency, amount in sorted(values.items())) or "nu există valori clasificate"
    return (f"{title} pentru {month}: {formatted}. Valori agregate, estimative și fără conversie între monede.", None)


def claim_message(db, message_id: str, now: int) -> bool:
    if not message_id or len(message_id) > 256:
        return False
    with db:
        db.execute("DELETE FROM whatsapp_webhook_receipts WHERE received_at < ?", (now - 30 * 86400,))
        cursor = db.execute("""INSERT INTO whatsapp_webhook_receipts(message_id,received_at,claimed_until,status)
            VALUES(?,?,?,'processing') ON CONFLICT(message_id) DO UPDATE SET
            received_at=excluded.received_at,claimed_until=excluded.claimed_until,status='processing'
            WHERE whatsapp_webhook_receipts.status!='completed' AND whatsapp_webhook_receipts.claimed_until < ?""",
            (message_id, now, now + 300, now))
        return cursor.rowcount == 1


def complete_message(db, message_id: str) -> None:
    with db:
        db.execute("UPDATE whatsapp_webhook_receipts SET status='completed',claimed_until=0 WHERE message_id=?",
                   (message_id,))


def release_message(db, message_id: str) -> None:
    with db:
        db.execute("DELETE FROM whatsapp_webhook_receipts WHERE message_id=? AND status='processing'", (message_id,))


def process_message(db, message: dict, config: WhatsAppConfig, now: int,
                    send_text: Callable[[str, str], None],
                    send_pdf: Callable[[str, Path, str], None],
                    report_writer: Callable[[object, str, Path], None]) -> str:
    """Process one verified inbound text; message body and phone are never persisted."""
    if not isinstance(message, dict) or message.get("type") != "text":
        return "ignored"
    sender = message.get("from")
    message_id = message.get("id")
    text_obj = message.get("text")
    timestamp = message.get("timestamp")
    if not isinstance(sender, str) or sender not in config.allowed_senders:
        return "unauthorized"
    if not isinstance(message_id, str) or not isinstance(text_obj, dict) or not isinstance(text_obj.get("body"), str):
        return "invalid"
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return "invalid"
    if sent_at > now + 300 or sent_at < now - 24 * 3600:
        return "outside_window"
    body = text_obj["body"][:2000]
    if not claim_message(db, message_id, now):
        return "duplicate"
    try:
        if not config.messaging_ready:
            raise MetaAPIError("Mesageria WhatsApp nu este activată în configurație.")
        answer, report_month = financial_answer(db, body, sent_at)
        if report_month:
            if not config.allow_reports:
                send_text(sender, "Trimiterea rapoartelor PDF este dezactivată de proprietar.")
            else:
                from tempfile import TemporaryDirectory
                with TemporaryDirectory(prefix="asistent-financiar-whatsapp-") as folder:
                    path = Path(folder) / f"raport-financiar-{report_month}.pdf"
                    report_writer(db, report_month, path)
                    send_pdf(sender, path, f"Raport financiar agregat {report_month}, solicitat de tine.")
        else:
            send_text(sender, answer)
        complete_message(db, message_id)
        return "processed"
    except Exception:
        release_message(db, message_id)
        raise


def parse_text_messages(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    messages: list[dict] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            value = change.get("value", {}) if isinstance(change, dict) else {}
            if isinstance(value, dict):
                for message in value.get("messages", []):
                    if isinstance(message, dict):
                        messages.append(message)
    return messages
