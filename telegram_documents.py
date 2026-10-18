"""Private Telegram document intake with a preview/confirm step.

Only formats parsed by existing, deterministic project parsers are staged.
Original file bytes are held in a short-lived temporary directory and are not
written to the ledger or logs. Parsed drafts live in process memory for 30 min.
"""
from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from booking_imports import BookingRecord, import_booking_csv
from finance_store import (Observation, connect, import_booking_records,
                           import_creasoft_report, import_observations)
from financial_imports import FinancialRecord, import_csv, import_mt940, import_xlsx
from main import citeste_raport

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_ROWS = 5000
MAX_XLSX_UNPACKED_BYTES = 50 * 1024 * 1024
DRAFT_TTL_SECONDS = 30 * 60
_DRAFTS: dict[str, dict] = {}
_DRAFT_LOCK = threading.Lock()

# This is the same explicit starter mapping presented in the web UI. It is
# never inferred from unknown headers; parse failure requires manual review.
_BANK_MAPPING = {
    "source": "bank", "currency": "RON", "date_format": "%d.%m.%Y",
    "delimiter": ";", "columns": {"date": "DATA", "description": "DESCRIERE", "amount": "SUMA"},
}

_CURRENCY_LABELS = {"RON": "lei", "EUR": "euro", "USD": "dolari"}


def _money(value: Decimal | None, currency: str) -> str:
    if value is None:
        return "nu apare în raport"
    formatted = f"{value:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{formatted} {_CURRENCY_LABELS.get(currency, currency or 'monedă neprecizată')}"


def _amounts_by_currency(rows: list[dict], field: str, currency_field: str,
                         *, negate: bool = False) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        raw = row.get(field)
        if raw is None or raw == "":
            continue
        currency = row.get(currency_field) or "monedă neprecizată"
        amount = Decimal(raw)
        totals[currency] = totals.get(currency, Decimal(0)) + (-amount if negate else amount)
    return totals


def _amount_label(totals: dict[str, Decimal], *, empty: str = "nu apare în raport") -> str:
    if not totals:
        return empty
    return "; ".join(_money(amount, currency) for currency, amount in sorted(totals.items()))


def _booking_preview(rows: list[dict]) -> tuple[str, str]:
    reservations = [row for row in rows
                    if row["record_type"].casefold().strip("() ") in {"rezervare", "reservation"}]
    payouts = [row for row in rows
               if row["record_type"].casefold().strip("() ") == "payout"]
    gross = _amounts_by_currency(reservations, "gross", "transaction_currency")
    commission = _amounts_by_currency(reservations, "commission", "transaction_currency", negate=True)
    other_fees: dict[str, Decimal] = {}
    for row in reservations:
        currency = row.get("transaction_currency") or "monedă neprecizată"
        fees = sum((Decimal(row[field]) for field in ("service_fee", "vat", "tax") if row.get(field)), Decimal(0))
        other_fees[currency] = other_fees.get(currency, Decimal(0)) - fees
    net = _amounts_by_currency(reservations, "net_payable", "transaction_currency")
    paid = _amounts_by_currency(payouts, "payout_value", "payout_currency")
    for currency in gross:
        commission.setdefault(currency, Decimal(0))
        other_fees.setdefault(currency, Decimal(0))
    for currency in net:
        paid.setdefault(currency, Decimal(0))
    remaining = {currency: max(amount - paid.get(currency, Decimal(0)), Decimal(0))
                 for currency, amount in net.items()}

    summary = [
        "Raport Booking",
        f"- Total rezervări: {_amount_label(gross)}",
        f"- Comisioane Booking: {_amount_label(commission, empty='0,00 lei')}",
        f"- Alte taxe: {_amount_label(other_fees, empty='0,00 lei')}",
        f"- Net de primit: {_amount_label(net)}",
        f"- Deja plătit de Booking: {_amount_label(paid, empty='0,00 lei')}",
        f"- Rămas de primit: {_amount_label(remaining)}",
    ]
    if remaining:
        conclusion = f"Booking mai are de plătit: {_amount_label(remaining)}."
    else:
        conclusion = "Nu pot calcula suma rămasă de primit din acest raport."
    summary.extend(["", f"Concluzie: {conclusion}", "", "Atenție: plățile din raportul Booking nu sunt confirmate în bancă până nu apar în extras."])

    details = ["Detalii Booking"]
    for row in reservations[:30]:
        currency = row.get("transaction_currency") or "monedă neprecizată"
        commission_value = Decimal(row["commission"]) if row.get("commission") else None
        fees = sum((Decimal(row[field]) for field in ("service_fee", "vat", "tax") if row.get(field)), Decimal(0))
        details.append(
            f"- Rezervare: brut {_money(Decimal(row['gross']) if row.get('gross') else None, currency)} · "
            f"comision {_money(-commission_value if commission_value is not None else None, currency)} · "
            f"alte taxe {_money(-fees, currency)} · net {_money(Decimal(row['net_payable']) if row.get('net_payable') else None, currency)}"
        )
    for row in payouts[:30]:
        currency = row.get("payout_currency") or "monedă neprecizată"
        when = row.get("payout_date") or "dată neprecizată"
        details.append(f"- Booking indică o plată: {_money(Decimal(row['payout_value']) if row.get('payout_value') else None, currency)} · {when} · neconfirmată în bancă")
    return "\n".join(summary), "\n".join(details)


def _store_draft(user_id: int, kind: str, digest: str, rows: list[dict],
                 summary: str, details: str, now: int) -> str:
    token = uuid.uuid4().hex
    with _DRAFT_LOCK:
        for old in [key for key, item in _DRAFTS.items() if item["expires"] <= now]:
            _DRAFTS.pop(old, None)
        if len(_DRAFTS) >= 20:
            raise ValueError("Există deja prea multe documente în așteptarea confirmării. Confirmă sau anulează unul.")
        _DRAFTS[token] = {"user_id": user_id, "kind": kind, "digest": digest,
                          "rows": rows, "summary": summary, "details": details,
                          "expires": now + DRAFT_TTL_SECONDS}
    return token


def _check_rows(rows: list) -> None:
    if len(rows) > MAX_DOCUMENT_ROWS:
        raise ValueError(f"Documentul depășește limita de {MAX_DOCUMENT_ROWS} de rânduri. Nu am importat date.")


def _check_xlsx_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            unpacked = sum(entry.file_size for entry in entries)
            if (len(entries) > 200 or unpacked > MAX_XLSX_UNPACKED_BYTES
                    or any(entry.file_size > 0 and entry.compress_size == 0 for entry in entries)
                    or any(entry.compress_size and entry.file_size / entry.compress_size > 100 for entry in entries)):
                raise ValueError("Fișierul Excel este prea complex sau comprimat cu un raport nesigur.")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Fișierul Excel nu este un document XLSX valid.") from exc


def analyze_document(user_id: int, filename: str, content: bytes, now: int | None = None) -> tuple[str, str, str]:
    """Parse a supported attachment and stage normalized rows without importing."""
    now = int(time.time()) if now is None else now
    if not isinstance(content, bytes) or not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Fișierul este gol sau depășește limita de 10 MB.")
    ext = Path(filename or "").suffix.casefold()
    if ext in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Acest PDF sau această fotografie nu poate fi citită în siguranță încă. Nu am extras și nu am salvat date. Folosește temporar importul manual acceptat în aplicație.")
    if ext not in {".csv", ".xlsx", ".sta", ".940", ".mt940"}:
        raise ValueError("Format neacceptat. Pot analiza momentan CSV, XLSX și MT940.")

    digest = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory(prefix="finance-telegram-") as folder:
        path = Path(folder) / ("document" + ext)
        path.write_bytes(content)
        if ext == ".csv":
            try:
                records = import_booking_csv(path)
                rows = [asdict(record) for record in records]
                _check_rows(rows)
                summary, details = _booking_preview(rows)
                return _store_draft(user_id, "booking", digest, rows, summary, details, now), summary, details
            except ValueError:
                pass
            try:
                aggregates = citeste_raport(path)
                rows = [{"label": label, "quantity": str(values["cantitate"]),
                         "sales": str(values["incasari"])} for label, values in aggregates.items()]
                _check_rows(rows)
                total = sum((Decimal(row["sales"]) for row in rows), Decimal(0))
                sales_text = _money(total, "RON")
                summary = (f"Raport Creasoft\n- Aparate raportate: {len(rows)}\n- Vânzări totale: {sales_text}\n\n"
                           "Observații:\n- Raportul nu separă numerarul de plățile cu cardul.\n"
                           "- Nu arată câți bani au rămas în aparate.\n\n"
                           f"Concluzie: Vânzări raportate de {sales_text}.")
                details = "Detalii aparate\n" + "\n".join(
                    f"- {r['label']}: {r['quantity']} produse · {_money(Decimal(r['sales']), 'RON')} vânzări"
                    for r in rows[:30])
                return _store_draft(user_id, "creasoft", digest, rows, summary, details, now), summary, details
            except ValueError:
                pass
            try:
                # Validate the delimiter from the actual header, but keep field
                # names, date format, currency and column roles explicit.
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    sample = stream.read(8192)
                mapping = {**_BANK_MAPPING, "columns": dict(_BANK_MAPPING["columns"])}
                try:
                    mapping["delimiter"] = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
                except csv.Error:
                    mapping["delimiter"] = ";"
                records = import_csv(path, mapping)
            except ValueError as exc:
                raise ValueError("CSV-ul nu corespunde formatului Booking, Creasoft sau mapării bancare cunoscute. Nicio tranzacție nu a fost salvată; folosește maparea manuală din aplicație.") from exc
        elif ext == ".xlsx":
            try:
                _check_xlsx_archive(path)
                records = import_xlsx(path, _BANK_MAPPING)
            except (ValueError, ImportError) as exc:
                raise ValueError("Excel-ul nu corespunde mapării bancare sigure DATA / DESCRIERE / SUMA (RON). Nicio tranzacție nu a fost salvată.") from exc
        else:
            try:
                records = import_mt940(path)
            except ValueError as exc:
                raise ValueError("Fișierul MT940 nu a putut fi validat. Nicio tranzacție nu a fost salvată.") from exc

    rows = [asdict(record) for record in records]
    _check_rows(rows)
    for row in rows:
        row["source_file"] = "telegram-document"
    credits = sum((Decimal(row["amount"]) for row in rows if Decimal(row["amount"]) > 0), Decimal(0))
    debits = sum((Decimal(row["amount"]) for row in rows if Decimal(row["amount"]) < 0), Decimal(0))
    summary = (f"Extras bancar\n- Tranzacții: {len(rows)}\n"
               f"- Încasări: {sum(1 for row in rows if Decimal(row['amount']) > 0)} · {_money(credits, 'RON')}\n"
               f"- Plăți: {sum(1 for row in rows if Decimal(row['amount']) < 0)} · {_money(abs(debits), 'RON')}\n\n"
               "Concluzie: Acestea sunt mișcările din extras; soldul se citește separat din extras.")
    details = "Detalii tranzacții\n" + "\n".join(
        f"- {r['transaction_date']}: {r['description'][:100]} · {_money(Decimal(r['amount']), r['currency'])}"
        for r in rows[:30])
    return _store_draft(user_id, "bank", digest, rows, summary, details, now), summary, details


def draft_preview(token: str, user_id: int, now: int | None = None) -> dict | None:
    now = int(time.time()) if now is None else now
    with _DRAFT_LOCK:
        draft = _DRAFTS.get(token)
        if not draft or draft["expires"] <= now or draft["user_id"] != user_id:
            _DRAFTS.pop(token, None) if draft and draft["expires"] <= now else None
            return None
        return dict(draft)


def finish_draft(db_path: Path, token: str, user_id: int, *, confirm: bool,
                 now: int | None = None) -> str:
    draft = draft_preview(token, user_id, now)
    if not draft:
        return "Cererea de import a expirat sau nu aparține contului tău. Trimite din nou documentul dacă este necesar."
    if not confirm:
        with _DRAFT_LOCK:
            _DRAFTS.pop(token, None)
        return "Import anulat. Nu am adăugat date în evidența financiară."
    with closing(connect(db_path)) as db:
        if draft["kind"] == "booking":
            with db:
                _, count = import_booking_records(db, "telegram-booking.csv", None,
                    [BookingRecord(**row) for row in draft["rows"]], content_sha256=draft["digest"])
        elif draft["kind"] == "creasoft":
            aggregates = {row["label"]: {"cantitate": Decimal(row["quantity"]), "incasari": Decimal(row["sales"])}
                         for row in draft["rows"]}
            with db:
                _, count = import_creasoft_report(db, "telegram-creasoft.csv", None, aggregates,
                                                   content_sha256=draft["digest"])
        else:
            observations = [Observation(
                source=row["source"], occurred_on=row["transaction_date"], description=row["description"],
                amount=row["amount"], currency=row["currency"], activity=row.get("activity", "unassigned"),
                kind=row.get("kind", "unclassified"), settlement=row.get("settlement", "recorded"),
                external_id=row.get("external_id", ""), counterparty=row.get("counterparty", ""),
                source_row=row["source_row"], raw=None) for row in draft["rows"]]
            with db:
                _, count = import_observations(db, "telegram-bank-statement", None, observations,
                                               content_sha256=draft["digest"])
    with _DRAFT_LOCK:
        _DRAFTS.pop(token, None)
    if count == 0:
        title = {"booking": "Raport Booking", "creasoft": "Raport Creasoft", "bank": "Extras bancar"}[draft["kind"]]
        return f"{title}\nAcest document fusese deja importat. Nu am adăugat duplicate."
    title = {"booking": "Raport Booking", "creasoft": "Raport Creasoft", "bank": "Extras bancar"}[draft["kind"]]
    imported = {"booking": f"Import confirmat: {count} rezervări/plăți.",
                "creasoft": f"Import confirmat: {count} aparate.",
                "bank": f"Import confirmat: {count} tranzacții."}[draft["kind"]]
    summary_body = draft["summary"].partition("\n")[2]
    return f"{title}\n{imported}\n\n{summary_body}"

