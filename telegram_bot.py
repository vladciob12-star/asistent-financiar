"""Opt-in Telegram Bot API integration for the private financial assistant."""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_store import monthly_summary
from whatsapp import financial_answer


MAX_WEBHOOK_BYTES = 1_000_000
MAX_API_RESPONSE_BYTES = 64_000
SECRET_RE = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
MACHINE_ACTIVITY = {
    "primul": "vending_oradea_era",
    "1": "vending_oradea_era",
    "era": "vending_oradea_era",
    "oradea era": "vending_oradea_era",
    "al doilea": "vending_crius_shopping_center",
    "doilea": "vending_crius_shopping_center",
    "2": "vending_crius_shopping_center",
    "crius": "vending_crius_shopping_center",
    "crisul": "vending_crius_shopping_center",
    "crisu": "vending_crius_shopping_center",
    "crisu shopping center": "vending_crius_shopping_center",
}
MACHINE_LABELS = {
    "vending_oradea_era": "primul tonomat",
    "vending_crius_shopping_center": "al doilea tonomat",
}


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    webhook_secret: str
    allowed_user_ids: frozenset[int]
    enable_messaging: bool
    allow_reports: bool

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        ids: set[int] = set()
        for item in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(","):
            value = item.strip()
            if value.isascii() and value.isdecimal() and int(value) > 0:
                ids.add(int(value))
        return cls(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip(),
            allowed_user_ids=frozenset(ids),
            enable_messaging=os.environ.get("TELEGRAM_ENABLE_MESSAGING", "").casefold() == "true",
            allow_reports=os.environ.get("TELEGRAM_ALLOW_REPORTS", "").casefold() == "true",
        )

    @property
    def webhook_ready(self) -> bool:
        return bool(self.enable_messaging and self.bot_token and SECRET_RE.fullmatch(self.webhook_secret)
                    and self.allowed_user_ids)


class TelegramAPIError(RuntimeError):
    """Sanitized error; never includes the bot token or API response body."""


class TelegramBotAPI:
    def __init__(self, config: TelegramConfig):
        if not config.webhook_ready:
            raise ValueError("Configurația Telegram este incompletă.")
        self.config = config
        self.base_url = f"https://api.telegram.org/bot{config.bot_token}"

    def _post(self, method: str, data: bytes, content_type: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/{method}", data=data, method="POST",
            headers={"Content-Type": content_type, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                if len(raw) > MAX_API_RESPONSE_BYTES:
                    raise TelegramAPIError("Răspuns Telegram prea mare.")
        except (urllib.error.URLError, TimeoutError, OSError):
            # Do not chain URL-bearing exceptions: Telegram embeds the bot token in request URLs.
            raise TelegramAPIError("Conexiunea Telegram a eșuat.") from None
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramAPIError("Răspuns Telegram invalid.") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise TelegramAPIError("Telegram nu a confirmat operația.")
        return result

    def _get(self, url: str, limit: int) -> bytes:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise TelegramAPIError("Fișierul Telegram depășește limita de siguranță.")
                return raw
        except TelegramAPIError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            # Telegram includes the bot token in Bot API and file URLs.
            raise TelegramAPIError("Descărcarea documentului Telegram a eșuat.") from None

    def download_document(self, file_id: str) -> tuple[str, bytes]:
        if not isinstance(file_id, str) or not file_id or len(file_id) > 256:
            raise TelegramAPIError("Identificatorul documentului Telegram este invalid.")
        result = self._post("getFile", json.dumps({"file_id": file_id}).encode(), "application/json")
        file_info = result.get("result")
        if not isinstance(file_info, dict):
            raise TelegramAPIError("Telegram nu a returnat informațiile fișierului.")
        path = file_info.get("file_path")
        size = file_info.get("file_size", 0)
        if (not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts
                or not isinstance(size, int) or size <= 0 or size > 10 * 1024 * 1024):
            raise TelegramAPIError("Fișierul este gol sau depășește limita de 10 MB.")
        from urllib.parse import quote
        url = f"https://api.telegram.org/file/bot{self.config.bot_token}/{quote(path, safe='/')}"
        content = self._get(url, 10 * 1024 * 1024)
        if len(content) != size:
            raise TelegramAPIError("Descărcarea documentului Telegram este incompletă.")
        return Path(path).name, content

    def send_document_preview(self, chat_id: int, text: str, token: str) -> None:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Confirmă importul", "callback_data": f"imp:{token}:y"},
            {"text": "❌ Anulează", "callback_data": f"imp:{token}:n"},
        ], [{"text": "🔍 Vezi detalii", "callback_data": f"imp:{token}:d"}]]}
        body = json.dumps({"chat_id": chat_id, "text": text[:4096], "reply_markup": keyboard},
                          ensure_ascii=False).encode("utf-8")
        self._post("sendMessage", body, "application/json; charset=utf-8")

    def answer_callback(self, callback_id: str) -> None:
        if isinstance(callback_id, str) and callback_id and len(callback_id) <= 256:
            self._post("answerCallbackQuery", json.dumps({"callback_query_id": callback_id}).encode(),
                       "application/json; charset=utf-8")

    def send_text(self, chat_id: int, text: str) -> None:
        body = json.dumps({"chat_id": chat_id, "text": text[:4096]}, ensure_ascii=False).encode("utf-8")
        self._post("sendMessage", body, "application/json; charset=utf-8")

    def send_pdf(self, chat_id: int, path: Path, caption: str) -> None:
        boundary = "----finance-telegram-" + uuid.uuid4().hex
        filename = Path(path).name
        pdf = Path(path).read_bytes()
        if len(pdf) > 10 * 1024 * 1024:
            raise TelegramAPIError("Raportul PDF depășește limita de siguranță.")
        chunks = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1024]}\r\n".encode(),
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
             "Content-Type: application/pdf\r\n\r\n").encode(),
            pdf,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        self._post("sendDocument", b"".join(chunks), f"multipart/form-data; boundary={boundary}")


def _month_label(now: int) -> str:
    return date.fromtimestamp(now).strftime("%Y-%m")


def _money_rows(rows: list[dict]) -> str:
    if not rows:
        return "nu există sume înregistrate"
    return "; ".join(f"{row['amount']} {row['currency']}" for row in rows)



def _normalize_question(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(char if char.isalnum() else " " for char in decomposed
                            if not unicodedata.combining(char)).split())


def latest_financial_update(db) -> str | None:
    """Latest import/balance timestamp; SQLite CURRENT_TIMESTAMP is UTC."""
    row = db.execute("""SELECT max(updated_at) AS updated_at FROM (
        SELECT max(imported_at) AS updated_at FROM imports
        UNION ALL SELECT max(created_at) AS updated_at FROM bank_balances
    )""").fetchone()
    return row["updated_at"] if row and row["updated_at"] else None


def _machine_answer(db, text: str, now: int) -> str | None:
    normalized = _normalize_question(text)
    if "tonomat" not in normalized and "automat" not in normalized:
        return None
    activity = next((activity for alias, activity in MACHINE_ACTIVITY.items()
                     if re.search(rf"(?<![a-z0-9]){re.escape(_normalize_question(alias))}(?![a-z0-9])", normalized)), None)
    if activity is None:
        return "Nu pot identifica tonomatul. Întreabă despre primul sau al doilea tonomat."
    label = MACHINE_LABELS[activity]
    month = _month_label(now)
    report = monthly_summary(db, month)
    values = report["activities"].get(activity, {})
    if values:
        monthly = "; ".join(
            f"venit {v['income']} {currency}, cheltuieli și comisioane "
            f"{(Decimal(v['expense']) + Decimal(v['commission'])):.2f} {currency}, rezultat estimat "
            f"{v['estimated_result']} {currency}"
            for currency, v in sorted(values.items())
        )
        return (f"Pentru {label}, în {month}: {monthly}. Acestea sunt valori clasificate, nu sold de numerar "
                "confirmat în aparat. Rapoartele Creasoft nu stabilesc soldul fizic disponibil.")
    latest = db.execute("""SELECT machine_label,reported_sales,currency FROM creasoft_reports
        WHERE activity=? ORDER BY import_id DESC,id DESC LIMIT 1""", (activity,)).fetchone()
    if latest:
        sales = Decimal(latest["reported_sales"])
        return (f"Cel mai recent raport Creasoft disponibil pentru {label} arată vânzări raportate de "
                f"{sales:.2f} {latest['currency']}, dar perioada nu este precizată. "
                "Raportul combină numerarul și plățile non-cash; nu reprezintă banii disponibili în aparat.")
    return f"Nu există date înregistrate pentru {label}. Importă manual raportul Creasoft ca să apară informații."


def answer_message(db, text: str, now: int, allow_reports: bool) -> tuple[str, str | None]:
    normalized = " ".join(text.casefold().split())
    greeting = normalized.strip("!?,. ")
    if greeting in {"salut", "bună", "buna", "bună ziua", "buna ziua", "hello", "hi", "/start"}:
        return ("Salut! Sunt asistentul tău financiar. Poți întreba despre venituri, cheltuieli, profit estimat, "
                "bani disponibili, Booking sau unul dintre tonomate. Pentru comenzile complete scrie /ajutor.", None)
    if normalized.startswith("/ajutor") or normalized in {"ajutor", "help", "/help"}:
        return ("Poți întreba: «Câți bani am disponibili?», «Cât am încasat luna aceasta?», «Ce profit am?», "
                "«Câți bani am în primul tonomat?» sau «Ce sume am de primit de la Booking?». "
                "Pentru PDF scrie «Trimite-mi raportul financiar lunar» ori /raport AAAA-LL. "
                "Sunt folosite doar datele existente, fără conversii valutare. Extrasele bancare se importă manual.", None)
    if (normalized.startswith("/raport ") or normalized in {"/raport", "raport pdf"}
            or "raportul financiar lunar" in normalized or "raport financiar lunar" in normalized):
        month_match = re.search(r"(?<!\d)(20\d{2}-(?:0[1-9]|1[0-2]))(?!\d)", normalized)
        month = month_match.group(1) if month_match else _month_label(now)
        return f"Pregătesc raportul financiar agregat pentru {month}.", month
    if ("tonomat" not in normalized and "automat" not in normalized
            and any(term in normalized for term in ("bani am", "bani disponibili", "fonduri disponibile", "câți bani", "cati bani"))):
        report = monthly_summary(db, _month_label(now))
        balances = report["bank_available_balances"]
        bank = "; ".join(f"{row['amount']} {row['currency']} (sold introdus manual la {row['as_of']})"
                          for row in balances) or "sold bancar necunoscut; nu există un sold introdus manual"
        booking = _money_rows(report["booking_receivables_not_cash"])
        return (f"Bani disponibili confirmați: {bank}. Numerarul din tonomatele ERA și Crișul nu poate fi stabilit "
                "din datele existente: rapoartele Creasoft disponibile combină cash și non-cash și nu confirmă numerarul fizic. "
                f"Sume Booking raportate/de primit: {booking}; sunt separate și nu sunt incluse în banii disponibili. "
                "Valorile nu se adună între monede diferite.", None)
    if "booking" in normalized or "de primit" in normalized or "neîncasat" in normalized or "neincasat" in normalized:
        month = _month_label(now)
        report = monthly_summary(db, month)
        receivables = _money_rows(report["booking_receivables_not_cash"])
        payout = _money_rows(report["booking_reported_payouts_not_bank_confirmed"])
        return (f"Booking pentru {month}: sume de primit neîncasate: {receivables}. "
                f"Plăți raportate, încă neconfirmate în extrasul bancar: {payout}. "
                "Nu le adun la banii disponibili până la confirmarea manuală în extras.", None)
    machine = _machine_answer(db, text, now)
    if machine is not None:
        return machine, None
    return financial_answer(db, text, now)


def claim_update(db, update_id: int, now: int) -> bool:
    with db:
        db.execute("DELETE FROM telegram_webhook_receipts WHERE received_at < ?", (now - 30 * 86400,))
        cursor = db.execute("""INSERT INTO telegram_webhook_receipts(update_id,received_at,claimed_until,status)
            VALUES(?,?,?,'processing') ON CONFLICT(update_id) DO UPDATE SET
            received_at=excluded.received_at,claimed_until=excluded.claimed_until,status='processing'
            WHERE telegram_webhook_receipts.status!='completed' AND telegram_webhook_receipts.claimed_until < ?""",
            (update_id, now, now + 300, now))
        return cursor.rowcount == 1


def complete_update(db, update_id: int) -> None:
    with db:
        db.execute("UPDATE telegram_webhook_receipts SET status='completed',claimed_until=0 WHERE update_id=?",
                   (update_id,))


def release_update(db, update_id: int) -> None:
    with db:
        db.execute("DELETE FROM telegram_webhook_receipts WHERE update_id=? AND status='processing'", (update_id,))


def process_update(db, update: object, config: TelegramConfig, now: int, api: TelegramBotAPI,
                   report_writer, db_path: Path | None = None) -> str:
    if not isinstance(update, dict):
        return "invalid"
    update_id = update.get("update_id")
    message = update.get("message")
    callback = update.get("callback_query")
    if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
        return "ignored"
    if isinstance(callback, dict):
        sender = callback.get("from")
        callback_message = callback.get("message")
        chat = callback_message.get("chat") if isinstance(callback_message, dict) else None
        user_id = sender.get("id") if isinstance(sender, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if (isinstance(user_id, bool) or not isinstance(user_id, int) or user_id not in config.allowed_user_ids
                or not isinstance(chat, dict) or chat.get("type") != "private" or chat_id != user_id):
            return "unauthorized"
        if not claim_update(db, update_id, now):
            return "duplicate"
        try:
            api.answer_callback(callback.get("id", ""))
            data = callback.get("data", "")
            match = re.fullmatch(r"imp:([0-9a-f]{32}):([ynd])", data) if isinstance(data, str) else None
            if not match or db_path is None:
                api.send_text(chat_id, "Acțiunea nu este validă sau a expirat.")
            else:
                from telegram_documents import draft_preview, finish_draft
                token, action = match.groups()
                if action == "d":
                    draft = draft_preview(token, user_id, now)
                    api.send_text(chat_id, draft["details"] if draft else
                                  "Cererea de import a expirat sau nu aparține contului tău.")
                else:
                    api.send_text(chat_id, finish_draft(db_path, token, user_id,
                                                        confirm=action == "y", now=now))
            complete_update(db, update_id)
            return "processed"
        except Exception:
            release_update(db, update_id)
            raise
    if not isinstance(message, dict):
        return "ignored"
    sender = message.get("from")
    chat = message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return "ignored"
    user_id = sender.get("id")
    chat_id = chat.get("id")
    if (isinstance(user_id, bool) or not isinstance(user_id, int) or user_id not in config.allowed_user_ids
            or chat.get("type") != "private" or chat_id != user_id):
        return "unauthorized"
    text = message.get("text")
    document = message.get("document")
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        if not claim_update(db, update_id, now):
            return "duplicate"
        try:
            api.send_text(chat_id, "Nu pot citi fotografii sau bonuri în siguranță încă. Nu am descărcat și nu am salvat imaginea; nu folosesc OCR sau servicii AI externe.")
            complete_update(db, update_id)
            return "unsupported_photo"
        except Exception:
            release_update(db, update_id)
            raise
    if not isinstance(text, str) and not isinstance(document, dict):
        return "ignored"
    if isinstance(text, str) and (not text.strip() or len(text) > 2000):
        return "ignored"
    if not claim_update(db, update_id, now):
        return "duplicate"
    try:
        if isinstance(document, dict):
            if db_path is None:
                api.send_text(chat_id, "Importul documentelor nu este disponibil momentan.")
            else:
                from telegram_documents import analyze_document
                file_id = document.get("file_id")
                filename = document.get("file_name", "document")
                if not isinstance(file_id, str) or not isinstance(filename, str):
                    api.send_text(chat_id, "Documentul nu conține metadate valide.")
                else:
                    try:
                        safe_name, content = api.download_document(file_id)
                        token, summary, _details = analyze_document(user_id, filename or safe_name, content, now)
                    except ValueError as exc:
                        api.send_text(chat_id, f"Nu am salvat datele. {exc}")
                    else:
                        api.send_document_preview(chat_id,
                            f"{summary}\n\nNu am salvat încă date în evidența financiară. Verifică rezumatul și confirmă importul; cererea expiră în 30 de minute.",
                            token)
            complete_update(db, update_id)
            return "document_preview"
        answer, report_month = answer_message(db, text, now, config.allow_reports)
        if report_month:
            if not config.allow_reports:
                api.send_text(chat_id, "Trimiterea rapoartelor PDF este dezactivată în configurație.")
            else:
                from tempfile import TemporaryDirectory
                with TemporaryDirectory(prefix="asistent-financiar-telegram-") as folder:
                    path = Path(folder) / f"raport-financiar-{report_month}.pdf"
                    report_writer(db, report_month, path)
                    api.send_pdf(chat_id, path, f"Raport financiar agregat {report_month}, solicitat de tine.")
        else:
            updated_at = latest_financial_update(db)
            freshness = (f"Ultima actualizare a datelor: {updated_at} UTC."
                         if updated_at else "Nu există încă importuri financiare în aplicație.")
            api.send_text(chat_id, f"{answer}\n\n{freshness}")
        complete_update(db, update_id)
        return "processed"
    except Exception:
        release_update(db, update_id)
        raise


def verify_webhook_secret(config: TelegramConfig, provided: str) -> bool:
    import hmac
    return bool(config.webhook_secret and provided and hmac.compare_digest(config.webhook_secret, provided))


def set_webhook(config: TelegramConfig, public_url: str) -> None:
    """Register the HTTPS webhook; call only after the Railway endpoint is deployed."""
    from urllib.parse import urlsplit
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Webhook-ul Telegram necesită un URL HTTPS public valid.")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    body = json.dumps({"url": origin + "/webhook/telegram",
                       "secret_token": config.webhook_secret,
                       "allowed_updates": ["message", "callback_query"], "drop_pending_updates": False}).encode()
    TelegramBotAPI(config)._post("setWebhook", body, "application/json")


