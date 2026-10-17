"""Private, local SQLite ledger for imported financial evidence.

Imports are observations, not automatically recognized income. Booking payouts
are receivables until matched to a bank statement; ambiguous matches are flagged.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS imports (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, filename TEXT NOT NULL,
 sha256 TEXT NOT NULL UNIQUE, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 status TEXT NOT NULL DEFAULT 'completed', notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id),
 source TEXT NOT NULL, external_id TEXT NOT NULL DEFAULT '',
 occurred_on TEXT NOT NULL, description TEXT NOT NULL,
 amount TEXT NOT NULL, currency TEXT NOT NULL,
 activity TEXT NOT NULL DEFAULT 'unassigned', kind TEXT NOT NULL DEFAULT 'unclassified',
 settlement TEXT NOT NULL DEFAULT 'recorded', counterparty TEXT NOT NULL DEFAULT '',
 source_row INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL DEFAULT '{}',
 duplicate_of INTEGER REFERENCES observations(id),
 UNIQUE(import_id, source_row)
);
CREATE INDEX IF NOT EXISTS observations_date_idx ON observations(occurred_on);
CREATE INDEX IF NOT EXISTS observations_activity_idx ON observations(activity, kind);
CREATE TABLE IF NOT EXISTS reconciliations (
 id INTEGER PRIMARY KEY, left_id INTEGER NOT NULL REFERENCES observations(id),
 right_id INTEGER NOT NULL REFERENCES observations(id), confidence TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'needs_review', note TEXT NOT NULL DEFAULT '',
 UNIQUE(left_id, right_id), CHECK(left_id <> right_id)
);
CREATE TABLE IF NOT EXISTS booking_entries (
 id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id),
 source_row INTEGER NOT NULL, record_type TEXT NOT NULL, reference_hash TEXT NOT NULL DEFAULT '',
 issued_on TEXT, check_in TEXT, check_out TEXT, reservation_status TEXT NOT NULL DEFAULT '',
 gross TEXT, commission TEXT, service_fee TEXT, vat TEXT, tax TEXT,
 transaction_value TEXT, transaction_currency TEXT NOT NULL DEFAULT '',
 net_payable TEXT, payout_value TEXT, payout_currency TEXT NOT NULL DEFAULT '', payout_date TEXT,
 settlement_status TEXT NOT NULL DEFAULT 'reported_not_bank_confirmed',
 UNIQUE(import_id,source_row)
);
CREATE TABLE IF NOT EXISTS booking_reconciliations (
 id INTEGER PRIMARY KEY, booking_entry_id INTEGER NOT NULL REFERENCES booking_entries(id),
 bank_observation_id INTEGER NOT NULL REFERENCES observations(id), confidence TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'needs_review', note TEXT NOT NULL DEFAULT '',
 UNIQUE(booking_entry_id,bank_observation_id)
);
CREATE TABLE IF NOT EXISTS creasoft_reports (
 id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id),
 source_row INTEGER NOT NULL, machine_label TEXT NOT NULL, activity TEXT NOT NULL,
 quantity TEXT NOT NULL, reported_sales TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'RON',
 period_start TEXT, period_end TEXT, cash_status TEXT NOT NULL DEFAULT 'cash_and_non_cash_combined',
 review_status TEXT NOT NULL DEFAULT 'ready', UNIQUE(import_id,source_row)
);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY, period TEXT NOT NULL, generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'generated'
);
CREATE TABLE IF NOT EXISTS bank_balances (
 id INTEGER PRIMARY KEY, currency TEXT NOT NULL, amount TEXT NOT NULL,
 as_of TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS bank_balances_currency_date_idx ON bank_balances(currency,as_of,id);
CREATE TABLE IF NOT EXISTS whatsapp_webhook_receipts (
 message_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL,
 claimed_until INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'processing'
);
"""


def _normalize_machine_label(label: str) -> str:
    decomposed = unicodedata.normalize("NFKD", label.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


def canonical_creasoft_machine(label: str) -> tuple[str, str]:
    """Map only explicit, normalized aliases; unknown names require review."""
    normalized = _normalize_machine_label(label)
    aliases = {
        "vending_oradea_era": {"oradea era", "era oradea"},
        "vending_crius_shopping_center": {
            "crisu shopping center oradea", "crisul shopping center oradea",
            "shopping center crisu oradea", "shopping center crisul oradea",
        },
    }
    for activity, names in aliases.items():
        if normalized in names:
            return activity, "ready"
    return "unassigned", "needs_review"


def import_creasoft_report(db: sqlite3.Connection, filename: str, content: bytes,
                           aggregates: dict[str, dict[str, Decimal]]) -> tuple[int, int]:
    """Store Creasoft aggregates without inventing a report period or cash balance."""
    digest = hashlib.sha256(content).hexdigest()
    if not aggregates:
        raise ValueError("Raportul Creasoft nu conține agregate valide.")
    with db:
        found = db.execute("SELECT id FROM imports WHERE sha256=?", (digest,)).fetchone()
        if found:
            return found["id"], 0
        cur = db.execute("INSERT INTO imports(source,filename,sha256,notes) VALUES('creasoft',?,?,?)",
                         (Path(filename).name, digest,
                          "Agregat raportat; perioada necunoscută dacă exportul nu o specifică. Cash și non-cash combinate."))
        import_id = cur.lastrowid
        for source_row, (label, values) in enumerate(sorted(aggregates.items()), start=1):
            activity, review = canonical_creasoft_machine(label)
            quantity = Decimal(values["cantitate"])
            sales = Decimal(values["incasari"])
            if not quantity.is_finite() or not sales.is_finite() or quantity < 0:
                raise ValueError(f"Agregat Creasoft invalid pentru automatul {label!r}.")
            db.execute("""INSERT INTO creasoft_reports(import_id,source_row,machine_label,activity,
                quantity,reported_sales,review_status) VALUES(?,?,?,?,?,?,?)""",
                (import_id, source_row, label, activity, format(quantity, "f"), format(sales, "f"), review))
        return import_id, len(aggregates)


@dataclass(frozen=True)
class Observation:
    source: str
    occurred_on: str
    description: str
    amount: str
    currency: str
    activity: str = "unassigned"
    kind: str = "unclassified"
    settlement: str = "recorded"
    external_id: str = ""
    counterparty: str = ""
    source_row: int = 0
    raw: dict | None = None


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def import_observations(db: sqlite3.Connection, filename: str,
                        content: bytes, rows: Iterable[Observation]) -> tuple[int, int]:
    """Import a whole file atomically; identical bytes are never re-imported."""
    digest = hashlib.sha256(content).hexdigest()
    materialized = list(rows)
    with db:
        found = db.execute("SELECT id FROM imports WHERE sha256=?", (digest,)).fetchone()
        if found:
            return found["id"], 0
        if not materialized:
            raise ValueError("Fișierul nu conține înregistrări valide.")
        source = materialized[0].source
        cur = db.execute("INSERT INTO imports(source,filename,sha256) VALUES(?,?,?)",
                         (source, Path(filename).name, digest))
        import_id = cur.lastrowid
        inserted = 0
        for row in materialized:
            date.fromisoformat(row.occurred_on)
            amount = Decimal(row.amount)
            if not amount.is_finite() or len(row.currency) != 3:
                raise ValueError(f"Înregistrare invalidă la rândul {row.source_row}.")
            same = db.execute("""SELECT id,amount FROM observations
                WHERE source=? AND external_id<>'' AND external_id=? AND amount=? AND currency=?""",
                (row.source, row.external_id, str(amount), row.currency.upper())).fetchone() if row.external_id else None
            # Compare monetary values as Decimals so harmless formatting such as
            # 100, 100.0 and 100.00 cannot prevent a reconciliation proposal.
            if same and Decimal(same["amount"]) != amount:
                same = None
            if row.external_id and not same:
                possible_duplicate = db.execute("""SELECT id,amount FROM observations
                    WHERE source=? AND external_id=? AND currency=?""",
                    (row.source, row.external_id, row.currency.upper())).fetchall()
                same = next((candidate for candidate in possible_duplicate
                             if Decimal(candidate["amount"]) == amount), None)
            cur = db.execute("""INSERT INTO observations(import_id,source,external_id,occurred_on,
                description,amount,currency,activity,kind,settlement,counterparty,source_row,raw_json,duplicate_of)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (import_id,row.source,row.external_id,row.occurred_on,row.description,str(amount),
                 row.currency.upper(),row.activity,row.kind,row.settlement,row.counterparty,
                 row.source_row,json.dumps(row.raw or {},ensure_ascii=False),same["id"] if same else None))
            new_id=cur.lastrowid
            inserted += 1
            # Cross-source close matches are retained and explicitly need review.
            if row.source.lower() in {"bank", "banca", "extras bancar"}:
                matches = db.execute("""SELECT id,amount FROM observations WHERE id<>? AND source<>?
                    AND occurred_on BETWEEN date(?, '-3 day') AND date(?, '+3 day')
                    AND currency=? AND duplicate_of IS NULL""",
                    (new_id,row.source,row.occurred_on,row.occurred_on,row.currency.upper())).fetchall()
                for match in matches:
                    if Decimal(match["amount"]) != amount:
                        continue
                    db.execute("INSERT OR IGNORE INTO reconciliations(left_id,right_id,confidence,note) VALUES(?,?,?,?)",
                               (min(new_id,match["id"]),max(new_id,match["id"]),"possible","Sumă și dată apropiate; confirmare manuală necesară."))
                # Also check Booking payouts here, so the result does not depend
                # on whether the bank file or the Booking report was imported first.
                booking_matches = db.execute("""SELECT id,payout_value FROM booking_entries
                    WHERE record_type IN ('(Payout)','Payout') AND settlement_status='reported_not_bank_confirmed'
                    AND payout_currency=? AND payout_date BETWEEN date(?, '-3 day') AND date(?, '+3 day')
                    AND payout_value IS NOT NULL""",
                    (row.currency.upper(),row.occurred_on,row.occurred_on)).fetchall()
                for booking_match in booking_matches:
                    if Decimal(booking_match["payout_value"]) != amount:
                        continue
                    db.execute("INSERT OR IGNORE INTO booking_reconciliations(booking_entry_id,bank_observation_id,confidence,note) VALUES(?,?,?,?)",
                               (booking_match["id"],new_id,"possible","Sumă și dată apropiate; confirmarea rămâne manuală."))
        return import_id, inserted


def import_booking_records(db: sqlite3.Connection, filename: str, content: bytes,
                          records: Iterable) -> tuple[int, int]:
    """Store Booking amounts separately; payout lines are never bank receipts."""
    digest=hashlib.sha256(content).hexdigest()
    materialized=list(records)
    with db:
        found=db.execute("SELECT id FROM imports WHERE sha256=?",(digest,)).fetchone()
        if found:
            return found["id"],0
        if not materialized:
            raise ValueError("Raportul Booking nu conține rânduri valide.")
        cur=db.execute("INSERT INTO imports(source,filename,sha256) VALUES('booking',?,?)",
                       (Path(filename).name,digest))
        import_id=cur.lastrowid
        count=0
        for record in materialized:
            db.execute("""INSERT INTO booking_entries(import_id,source_row,record_type,reference_hash,
                issued_on,check_in,check_out,reservation_status,gross,commission,service_fee,vat,tax,
                transaction_value,transaction_currency,net_payable,payout_value,payout_currency,payout_date)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (import_id,record.source_row,record.record_type,record.reference_hash,record.issued_on,
                 record.check_in,record.check_out,record.reservation_status,record.gross,record.commission,
                 record.service_fee,record.vat,record.tax,record.transaction_value,record.transaction_currency,
                 record.net_payable,record.payout_value,record.payout_currency,record.payout_date))
            booking_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            count+=1
            if record.record_type.casefold() in {"(payout)","payout"} and record.payout_value and record.payout_date:
                matches=db.execute("""SELECT id,amount FROM observations WHERE source IN ('bank','banca','extras bancar')
                    AND settlement='received' AND currency=?
                    AND occurred_on BETWEEN date(?, '-3 day') AND date(?, '+3 day') AND duplicate_of IS NULL""",
                    (record.payout_currency,record.payout_date,record.payout_date)).fetchall()
                for match in matches:
                    if Decimal(match["amount"]) != Decimal(record.payout_value):
                        continue
                    db.execute("INSERT OR IGNORE INTO booking_reconciliations(booking_entry_id,bank_observation_id,confidence,note) VALUES(?,?,?,?)",
                        (booking_id,match["id"],"possible","Valoarea și data plății coincid; confirmarea rămâne manuală."))
        return import_id,count


def classify_observation(db: sqlite3.Connection, observation_id: int, *, activity: str, kind: str) -> None:
    """Explicit user classification; no automatic reassignment is performed."""
    with db:
        cur=db.execute("UPDATE observations SET activity=?,kind=? WHERE id=?",
                       (activity,kind,observation_id))
        if cur.rowcount != 1:
            raise ValueError("Tranzacția nu a fost găsită.")


def confirm_reconciliation(db: sqlite3.Connection, reconciliation_id: int) -> None:
    with db:
        cur=db.execute("UPDATE reconciliations SET status='confirmed' WHERE id=? AND status='needs_review'",
                       (reconciliation_id,))
        if cur.rowcount != 1:
            raise ValueError("Asocierea nu există sau a fost deja procesată.")


def confirm_booking_reconciliation(db: sqlite3.Connection, reconciliation_id: int) -> None:
    with db:
        relation=db.execute("SELECT booking_entry_id FROM booking_reconciliations WHERE id=? AND status='needs_review'",
                             (reconciliation_id,)).fetchone()
        if not relation:
            raise ValueError("Asocierea Booking nu există sau a fost deja procesată.")
        db.execute("UPDATE booking_reconciliations SET status='confirmed' WHERE id=?",(reconciliation_id,))
        db.execute("UPDATE booking_entries SET settlement_status='received' WHERE id=?",(relation["booking_entry_id"],))


def monthly_summary(db: sqlite3.Connection, month: str) -> dict:
    """Summarize classified income/expenses and distinct bank-confirmed funds."""
    date.fromisoformat(month + "-01")
    rows=db.execute("SELECT * FROM observations WHERE substr(occurred_on,1,7)=? AND duplicate_of IS NULL",(month,)).fetchall()
    totals: dict[str,dict[str,dict[str,Decimal]]]={}
    for row in rows:
        if row["kind"] not in {"income","expense","commission"}:
            continue
        # The bank is the authority for received payouts; a Booking statement is
        # supporting evidence, and must never add the same payout a second time.
        if row["source"] == "booking" and row["kind"] == "income":
            continue
        currency = row["currency"].upper()
        bucket=totals.setdefault(row["activity"],{}).setdefault(
            currency,{"income":Decimal(0),"expense":Decimal(0),"commission":Decimal(0)})
        amount=Decimal(row["amount"])
        bucket[row["kind"]]+=amount if row["kind"] == "income" else abs(amount)
    outstanding_rows=db.execute("""SELECT currency,amount FROM observations
        WHERE substr(occurred_on,1,7)=? AND source='booking' AND settlement IN ('scheduled','confirmed_unpaid')
        AND duplicate_of IS NULL""",(month,)).fetchall()
    booking_payout_rows=db.execute("""SELECT payout_currency AS currency,payout_value AS amount FROM booking_entries
        WHERE substr(payout_date,1,7)=? AND record_type IN ('(Payout)','Payout')
        AND settlement_status='reported_not_bank_confirmed' AND payout_value IS NOT NULL""",(month,)).fetchall()
    bank_rows=db.execute("""SELECT currency,amount FROM observations
        WHERE substr(occurred_on,1,7)=? AND source IN ('bank','banca','extras bancar')
        AND settlement='received' AND CAST(amount AS NUMERIC)>0 AND duplicate_of IS NULL""",(month,)).fetchall()
    def currency_totals(values):
        sums: dict[str,Decimal]={}
        for item in values:
            sums[item['currency']]=sums.get(item['currency'],Decimal(0))+Decimal(item['amount'])
        return [{"currency":currency,"amount":format(amount,'.2f')} for currency,amount in sorted(sums.items())]
    unclassified=[dict(r) for r in rows if r["kind"] in {"unclassified","unassigned"}]
    balances = db.execute("""SELECT b.currency,b.amount,b.as_of FROM bank_balances b
        WHERE NOT EXISTS (SELECT 1 FROM bank_balances newer WHERE newer.currency=b.currency
          AND (newer.as_of>b.as_of OR (newer.as_of=b.as_of AND newer.id>b.id)))
        ORDER BY b.currency""").fetchall()
    return {"period":month,"activities":{activity:{currency:{k:format(v,'.2f') for k,v in value.items()} |
        {"estimated_result":format(value['income']-value['expense']-value['commission'],'.2f')}
        for currency,value in currencies.items()} for activity,currencies in totals.items()},
        "bank_received_totals":currency_totals(bank_rows),"bank_available_balances":[dict(row) for row in balances],
        "booking_receivables_not_cash":currency_totals(outstanding_rows),
        "booking_reported_payouts_not_bank_confirmed":currency_totals(booking_payout_rows),
        "unclassified":unclassified,
        "creasoft_reports_without_period":[dict(r) for r in db.execute(
            "SELECT machine_label,activity,quantity,reported_sales,currency,cash_status,review_status "
            "FROM creasoft_reports WHERE period_start IS NULL AND period_end IS NULL ORDER BY id").fetchall()],
        "reconciliation_issues":[dict(r) for r in db.execute("SELECT * FROM reconciliations WHERE status='needs_review'").fetchall()] +
        [dict(r) for r in db.execute("SELECT * FROM booking_reconciliations WHERE status='needs_review'").fetchall()],
        "methodology":"Sunt însumate doar observațiile clasificate; plățile Booking programate nu sunt numerar, iar rezultatul este estimativ, nu profit contabil/fiscal."}


def record_bank_balance(db: sqlite3.Connection, amount: str, currency: str, as_of: str) -> int:
    """Record an explicitly user-entered statement balance, without a bank connection."""
    try:
        value = Decimal(amount)
    except InvalidOperation as exc:
        raise ValueError("Introdu un sold numeric valid.") from exc
    currency = currency.strip().upper()
    date.fromisoformat(as_of)
    if not value.is_finite() or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("Introdu un sold numeric și un cod de monedă ISO din 3 litere.")
    with db:
        cur = db.execute("INSERT INTO bank_balances(currency,amount,as_of) VALUES(?,?,?)",
                         (currency, format(value, "f"), as_of))
    return cur.lastrowid


def write_monthly_pdf_report(db: sqlite3.Connection, month: str, path: Path) -> None:
    """Create a local monthly PDF with explicit caveats about estimates and balances."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    report = monthly_summary(db, month)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    story = [Paragraph(f"Raport financiar lunar — {month}", styles["Title"]),
             Paragraph("Valori agregate separat pe monedă. Rezultatul este estimativ, nu profit contabil sau fiscal.", styles["BodyText"]),
             Spacer(1, 6 * mm)]
    rows = [["Activitate", "Monedă", "Venituri", "Cheltuieli", "Comisioane", "Rezultat estimat"]]
    for activity, currencies in sorted(report["activities"].items()):
        for currency, values in sorted(currencies.items()):
            rows.append([activity, currency, values["income"], values["expense"], values["commission"], values["estimated_result"]])
    if len(rows) == 1:
        rows.append(["Fără tranzacții clasificate", "—", "—", "—", "—", "—"])
    table = Table(rows, repeatRows=1, colWidths=[42*mm, 17*mm, 23*mm, 23*mm, 23*mm, 30*mm])
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17324d")),
                               ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                               ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#d5dde5")),
                               ("FONTSIZE", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                               ("TOPPADDING", (0,0), (-1,-1), 6), ("VALIGN", (0,0), (-1,-1), "TOP")] ))
    story.extend([table, Spacer(1, 6*mm), Paragraph("Bani disponibili (sold introdus manual din extras)", styles["Heading2"])])
    balances = report["bank_available_balances"]
    story.append(Paragraph("<br/>".join(f"{item['currency']}: {item['amount']} (la {item['as_of']})" for item in balances)
                           if balances else "Necunoscut — introdu manual un sold din extrasul bancar.", styles["BodyText"]))
    story.extend([Spacer(1, 4*mm), Paragraph("Încasări bancare în luna raportată", styles["Heading2"])])
    received = report["bank_received_totals"]
    story.append(Paragraph("<br/>".join(f"{item['currency']}: {item['amount']}" for item in received)
                           if received else "Nu există încasări bancare clasificate pentru această lună.", styles["BodyText"]))
    story.extend([Spacer(1, 4*mm), Paragraph("Creanțele Booking programate nu sunt bani disponibili. Rapoartele Creasoft fără perioadă validată nu sunt atribuite acestei luni. Datele rămân locale.", styles["BodyText"])])
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=16*mm, leftMargin=16*mm,
                      topMargin=16*mm, bottomMargin=16*mm, title=f"Raport financiar {month}").build(story)


def write_html_report(db: sqlite3.Connection, month: str, path: Path) -> None:
    report=monthly_summary(db,month)
    rows=[]
    for activity, currencies in report["activities"].items():
        for currency, values in currencies.items():
            rows.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _escape(activity),_escape(currency),values["income"],values["expense"],values["estimated_result"]))
    html="""<!doctype html><html lang='ro'><meta charset='utf-8'><title>Raport financiar {period}</title>
    <h1>Raport financiar — {period}</h1><p>Estimări bazate doar pe înregistrările clasificate.</p>
    <table><thead><tr><th>Activitate</th><th>Monedă</th><th>Venituri</th><th>Cheltuieli</th><th>Rezultat estimat</th></tr></thead>
    <tbody>{rows}</tbody></table><h2>Plăți Booking raportate, încă neconfirmate în bancă</h2><pre>{booking_payouts}</pre>
    <h2>Plăți Booking mapate ca neachitate</h2><pre>{receivables}</pre>
    <h2>Agregate Creasoft fără perioadă identificată (cash + non-cash)</h2><pre>{creasoft}</pre>
    <h2>Necesită clasificare</h2><pre>{unclassified}</pre><h2>Reconciliere de verificat</h2><pre>{issues}</pre>
    <p>{methodology}</p></html>""".format(period=_escape(month),rows="".join(rows),
        booking_payouts=_escape(str(report["booking_reported_payouts_not_bank_confirmed"])),
        receivables=_escape(str(report["booking_receivables_not_cash"])),
        creasoft=_escape(str(report["creasoft_reports_without_period"])),
        unclassified=_escape(str(report["unclassified"])),issues=_escape(str(report["reconciliation_issues"])),methodology=_escape(report["methodology"]))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(html,encoding="utf-8")
    with db: db.execute("INSERT INTO reports(period,path) VALUES(?,?)",(month,str(path)))


def _escape(value: str) -> str:
    return value.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#x27;")
