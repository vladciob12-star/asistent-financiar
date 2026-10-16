"""Local processor for Creasoft vending-machine sales reports."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


DIRECTOR_IMPLICIT = Path("rapoarte/creasoft")
COLOANA_VENDOMAT = "Vendomat"
COLOANA_CANTITATE = "Cantitate"
COLOANA_INCASARI = "Incasari totale (cash + non-cash - rest)"


def transforma_numar(valoare: str | None, *, fisier: Path | None = None, rand: int | None = None) -> Decimal:
    """Parse Romanian/European and plain decimal amounts without float rounding."""
    text = (valoare or "").strip().replace("lei", "").replace("RON", "").replace("ron", "")
    text = text.replace("\u00a0", "").replace(" ", "")
    if not text:
        text = "0"
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation as exc:
        context = f" în {fisier}" if fisier else ""
        context += f", rândul {rand}" if rand else ""
        raise ValueError(f"Număr invalid{context}: {valoare!r}") from exc


def citeste_raport(fisier: Path) -> dict[str, dict[str, Decimal]]:
    """Read and aggregate one semicolon-delimited Creasoft CSV report."""
    try:
        document = fisier.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Nu pot deschide {fisier}: {exc}") from exc

    with document:
        cititor = csv.DictReader(document, delimiter=";")
        if not cititor.fieldnames:
            raise ValueError(f"{fisier}: fișierul CSV nu are antet.")
        # Trim spaces in exported headings as well as the UTF-8 BOM.
        cititor.fieldnames = [n.strip().lstrip("\ufeff") for n in cititor.fieldnames]
        necesare = {COLOANA_VENDOMAT, COLOANA_CANTITATE, COLOANA_INCASARI}
        lipsa = necesare - set(cititor.fieldnames)
        if lipsa:
            raise ValueError(f"{fisier}: lipsesc coloanele: {', '.join(sorted(lipsa))}")

        rezultate: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: {"cantitate": Decimal(0), "incasari": Decimal(0)}
        )
        for nr_rand, rand in enumerate(cititor, start=2):
            if None in rand:
                raise ValueError(f"{fisier}, rândul {nr_rand}: prea multe valori față de antet.")
            vendomat = (rand.get(COLOANA_VENDOMAT) or "").strip()
            if not vendomat:
                continue
            cantitate = transforma_numar(rand.get(COLOANA_CANTITATE), fisier=fisier, rand=nr_rand)
            incasari = transforma_numar(rand.get(COLOANA_INCASARI), fisier=fisier, rand=nr_rand)
            if cantitate != cantitate.to_integral_value():
                raise ValueError(f"{fisier}, rândul {nr_rand}: cantitatea trebuie să fie număr întreg.")
            rezultate[vendomat]["cantitate"] += cantitate
            rezultate[vendomat]["incasari"] += incasari
        if not rezultate:
            raise ValueError(f"{fisier}: raportul nu conține înregistrări cu vendomat.")
        return dict(rezultate)


def proceseaza_rapoarte(director: Path, export: Path | None = None) -> int:
    """Print report totals, optionally saving a machine-readable summary."""
    if not director.is_dir():
        print(f"Folderul nu există: {director}", file=sys.stderr)
        return 1
    fisiere = sorted(p for p in director.glob("*.csv") if p.is_file())
    if not fisiere:
        print(f"Nu există rapoarte CSV în {director}.", file=sys.stderr)
        return 1

    randuri_export: list[dict[str, str]] = []
    erori = 0
    print("ASISTENT FINANCIAR — RAPOARTE CREASOFT")
    for fisier in fisiere:
        print(f"\n{fisier.name}")
        try:
            rezultate = citeste_raport(fisier)
        except ValueError as exc:
            print(f"  EROARE: {exc}", file=sys.stderr)
            erori += 1
            continue
        cantitate_totala = Decimal(0)
        incasari_totale = Decimal(0)
        for vendomat, date in sorted(rezultate.items()):
            cantitate = date["cantitate"]
            incasari = date["incasari"]
            cantitate_totala += cantitate
            incasari_totale += incasari
            print(f"  {vendomat}: {cantitate:.0f} produse | {incasari:.2f} lei")
            randuri_export.append({"fisier": fisier.name, "vendomat": vendomat,
                                   "cantitate": f"{cantitate:.0f}", "incasari_lei": f"{incasari:.2f}"})
        print(f"  TOTAL: {cantitate_totala:.0f} produse | {incasari_totale:.2f} lei")

    if export is not None:
        try:
            export.parent.mkdir(parents=True, exist_ok=True)
            with export.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["fisier", "vendomat", "cantitate", "incasari_lei"])
                writer.writeheader()
                writer.writerows(randuri_export)
        except OSError as exc:
            print(f"Nu pot salva exportul {export}: {exc}", file=sys.stderr)
            return 1
        print(f"\nRezumat salvat în {export}")
    return 1 if erori else 0


def main() -> int:
    # Windows consoles may default to cp1252, which cannot render Romanian
    # diacritics used in summaries and error messages.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Agregă vânzările din rapoarte CSV Creasoft.")
    parser.add_argument("--director", type=Path, default=DIRECTOR_IMPLICIT,
                        help=f"directorul cu rapoarte CSV (implicit: {DIRECTOR_IMPLICIT})")
    parser.add_argument("--export", type=Path, help="salvează detaliile agregate într-un CSV")
    parser.add_argument("--import-csv", "--import-file", dest="import_file", type=Path,
                        help="importă un extras CSV sau XLSX folosind o mapare explicită")
    parser.add_argument("--mapping", type=Path, help="configurația JSON cu antetele reale din CSV")
    parser.add_argument("--sheet", help="numele foii Excel (implicit: foaia activă)")
    parser.add_argument("--monthly-export", type=Path,
                        help="la import CSV, salvează totalurile lunare semnate pe monedă")
    parser.add_argument("--database", type=Path,
                        help="opțional: stochează și observațiile în registrul SQLite indicat")
    parser.add_argument("--report-month", help="generează raportul HTML pentru luna YYYY-MM din registrul SQLite")
    parser.add_argument("--report-html", type=Path, help="calea raportului HTML generat împreună cu --report-month")
    parser.add_argument("--import-booking-report", type=Path,
                        help="importă raportul CSV financiar Booking cu câmpurile monetare păstrate separat")
    parser.add_argument("--import-creasoft-report", type=Path,
                        help="importă agregatul CSV Creasoft în SQLite fără să deducă perioada raportului")
    args = parser.parse_args()
    if args.import_creasoft_report:
        if not args.database or args.import_file or args.import_booking_report or args.mapping or args.report_month or args.report_html:
            parser.error("--import-creasoft-report trebuie folosit singur împreună cu --database.")
        from finance_store import connect, import_creasoft_report
        try:
            aggregates = citeste_raport(args.import_creasoft_report)
            with connect(args.database) as db:
                _, saved_count = import_creasoft_report(db, args.import_creasoft_report.name,
                    args.import_creasoft_report.read_bytes(), aggregates)
        except (ValueError, OSError, sqlite3.Error) as exc:
            print(f"EROARE: {exc}", file=sys.stderr)
            return 1
        print(f"Importate {saved_count} agregate Creasoft în SQLite; perioada nu este dedusă, iar numerarul fizic nu este confirmat.")
        return 0
    if args.import_booking_report:
        if not args.database or args.import_file or args.mapping or args.report_month or args.report_html:
            parser.error("--import-booking-report trebuie folosit singur împreună cu --database.")
        from booking_imports import import_booking_csv
        from finance_store import connect, import_booking_records
        try:
            records=import_booking_csv(args.import_booking_report)
            with connect(args.database) as db:
                _,saved_count=import_booking_records(db,args.import_booking_report.name,
                    args.import_booking_report.read_bytes(),records)
        except (ValueError, OSError, sqlite3.Error) as exc:
            print(f"EROARE: {exc}",file=sys.stderr)
            return 1
        print(f"Importate {saved_count} rânduri Booking; plățile nu sunt considerate încasate fără reconciliere bancară.")
        return 0
    if args.report_month:
        if args.import_file or args.mapping or not args.database or not args.report_html:
            parser.error("--report-month trebuie folosit cu --database și --report-html, separat de un import.")
        from finance_store import connect, write_html_report
        try:
            with connect(args.database) as db:
                write_html_report(db, args.report_month, args.report_html)
        except (ValueError, OSError, sqlite3.Error) as exc:
            print(f"EROARE: {exc}", file=sys.stderr)
            return 1
        print(f"Raportul pentru {args.report_month} a fost salvat în {args.report_html}.")
        return 0
    if args.import_file or args.mapping:
        if not args.import_file or not args.mapping or not args.export:
            parser.error("--import-file, --mapping și --export trebuie folosite împreună.")
        from financial_imports import export_monthly_totals, export_records, import_csv, import_xlsx, load_mapping
        try:
            mapping = load_mapping(args.mapping)
            if args.import_file.suffix.lower() == ".csv":
                records = import_csv(args.import_file, mapping)
            elif args.import_file.suffix.lower() == ".xlsx":
                records = import_xlsx(args.import_file, mapping, sheet_name=args.sheet)
            else:
                parser.error("--import-file acceptă doar fișiere CSV sau XLSX.")
            export_records(records, args.export)
            if args.monthly_export:
                export_monthly_totals(records, args.monthly_export)
            if args.database:
                from finance_store import Observation, connect, import_observations
                observations = [Observation(
                    source=("booking" if "booking" in record.source.casefold() else
                            "bank" if "banc" in record.source.casefold() or record.source.casefold() == "bank" else
                            record.source.strip().lower()),
                    occurred_on=record.transaction_date,
                    description=record.description,
                    amount=record.amount,
                    currency=record.currency,
                    activity=record.activity or "unassigned",
                    kind=record.kind or "unclassified",
                    settlement=record.settlement or "recorded",
                    external_id=record.external_id,
                    counterparty=record.counterparty,
                    source_row=record.source_row,
                    raw={"source_file": record.source_file},
                ) for record in records]
                with connect(args.database) as db:
                    _, saved_count = import_observations(db, args.import_file.name,
                                                         args.import_file.read_bytes(), observations)
                print(f"Adăugate în registrul SQLite: {saved_count} observații.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            print(f"EROARE: {exc}", file=sys.stderr)
            return 1
        print(f"Importate {len(records)} înregistrări din {args.import_file.name}.")
        print(f"Rezumat normalizat salvat în {args.export}.")
        if args.monthly_export:
            print(f"Totalurile lunare salvate în {args.monthly_export}.")
        if args.database:
            print(f"Registrul SQLite actualizat: {args.database}.")
        return 0
    return proceseaza_rapoarte(args.director, args.export)


if __name__ == "__main__":
    raise SystemExit(main())
