"""Parser for the localized Booking.com financial payout CSV export.

Amounts remain in separate fields. A Booking-reported payout is never treated
as cash received until it is reconciled with an imported bank entry.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from main import transforma_numar


HEADERS = {
    "record_type": "Tip/Tip de tranzacție",
    "reference": "Numărul de referință",
    "issued_on": "Data emiterii",
    "check_in": "Data de check-in",
    "check_out": "Data de check-out",
    "reservation_status": "Statusul rezervării",
    "gross": "Suma brută",
    "commission": "Comision",
    "service_fee": "Taxă de serviciu pentru plăți",
    "vat": "TVA",
    "tax": "Impozit",
    "transaction_value": "Valoarea tranzacției",
    "transaction_currency": "Moneda tranzacției",
    "net_payable": "Suma de plată",
    "payout_value": "Valoarea plății",
    "payout_currency": "Moneda în care se efectuează plata",
    "payout_date": "Data plății",
}


@dataclass(frozen=True)
class BookingRecord:
    source_row: int
    record_type: str
    reference_hash: str
    issued_on: str | None
    check_in: str | None
    check_out: str | None
    reservation_status: str
    gross: str | None
    commission: str | None
    service_fee: str | None
    vat: str | None
    tax: str | None
    transaction_value: str | None
    transaction_currency: str
    net_payable: str | None
    payout_value: str | None
    payout_currency: str
    payout_date: str | None


def _optional_amount(value: str, path: Path, row: int) -> str | None:
    text = (value or "").strip()
    if not text or text in {"-", "—"}:
        return None
    return format(transforma_numar(text, fisier=path, rand=row), "f")


def _optional_date(value: str, path: Path, row: int) -> str | None:
    text = (value or "").strip()
    if not text or text in {"-", "—"}:
        return None
    for pattern in ("%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"{path}, rândul {row}: dată Booking nevalidă.")


def import_booking_csv(path: Path) -> list[BookingRecord]:
    """Parse exact financial columns from a Booking payout CSV; retain no PII."""
    try:
        stream = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Nu pot deschide {path}: {exc}") from exc
    records: list[BookingRecord] = []
    with stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
        reader = csv.DictReader(stream, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"{path}: CSV-ul nu are antet.")
        reader.fieldnames = [field.strip().lstrip("\ufeff") for field in reader.fieldnames]
        missing = set(HEADERS.values()) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: lipsesc coloanele Booking: {', '.join(sorted(missing))}")
        for line, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{path}, rândul {line}: număr incorect de coloane.")
            record_type = (row.get(HEADERS["record_type"]) or "").strip()
            reference = (row.get(HEADERS["reference"]) or "").strip()
            if not record_type:
                raise ValueError(f"{path}, rândul {line}: tip tranzacție Booking lipsă.")
            # Do not persist or export the raw reservation/reference identifier.
            reference_hash = hashlib.sha256(reference.encode("utf-8")).hexdigest() if reference else ""
            def amount(role: str) -> str | None:
                return _optional_amount(row.get(HEADERS[role]) or "", path, line)
            def day(role: str) -> str | None:
                return _optional_date(row.get(HEADERS[role]) or "", path, line)
            records.append(BookingRecord(
                source_row=line, record_type=record_type, reference_hash=reference_hash,
                issued_on=day("issued_on"), check_in=day("check_in"), check_out=day("check_out"),
                reservation_status=(row.get(HEADERS["reservation_status"]) or "").strip(),
                gross=amount("gross"), commission=amount("commission"), service_fee=amount("service_fee"),
                vat=amount("vat"), tax=amount("tax"), transaction_value=amount("transaction_value"),
                transaction_currency=(row.get(HEADERS["transaction_currency"]) or "").strip().upper(),
                net_payable=amount("net_payable"), payout_value=amount("payout_value"),
                payout_currency=(row.get(HEADERS["payout_currency"]) or "").strip().upper(),
                payout_date=day("payout_date"),
            ))
    if not records:
        raise ValueError(f"{path}: nu conține rânduri Booking.")
    for record in records:
        for currency in (record.transaction_currency, record.payout_currency):
            if currency and (len(currency) != 3 or not currency.isalpha()):
                raise ValueError(f"{path}, rândul {record.source_row}: cod de monedă Booking nevalid.")
    return records
