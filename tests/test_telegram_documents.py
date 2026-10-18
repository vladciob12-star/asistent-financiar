import csv
import io
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from booking_imports import HEADERS
from finance_store import connect
from telegram_documents import _check_rows, analyze_document, finish_draft


def booking_csv():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(HEADERS.values()))
    writer.writeheader()
    row = {field: "-" for field in HEADERS.values()}
    row.update({HEADERS["record_type"]: "Rezervare", HEADERS["reference"]: "FAKE-REF-1",
                HEADERS["issued_on"]: "01.09.2026", HEADERS["reservation_status"]: "OK",
                HEADERS["gross"]: "100,00", HEADERS["commission"]: "-15,00",
                HEADERS["transaction_value"]: "100,00", HEADERS["transaction_currency"]: "RON",
                HEADERS["net_payable"]: "85,00", HEADERS["payout_currency"]: "RON"})
    writer.writerow(row)
    return output.getvalue().encode()


def booking_csv_mixed_currencies():
    content = booking_csv().decode()
    reader = csv.DictReader(io.StringIO(content))
    fields = list(reader.fieldnames)
    rows = list(reader)
    second = dict(rows[0])
    second.update({HEADERS["record_type"]: "(Payout)", HEADERS["reference"]: "FAKE-REF-EUR",
                   HEADERS["gross"]: "20,00", HEADERS["commission"]: "-2,00",
                   HEADERS["transaction_currency"]: "EUR", HEADERS["payout_value"]: "3,00",
                   HEADERS["payout_currency"]: "EUR", HEADERS["payout_date"]: "05.09.2026"})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows + [second])
    return output.getvalue().encode()


def booking_csv_with_summary_totals():
    content = booking_csv().decode()
    reader = csv.DictReader(io.StringIO(content))
    fields = list(reader.fieldnames)
    reservation = next(reader)
    reservation.update({HEADERS["gross"]: "975,00", HEADERS["commission"]: "-146,25",
                        HEADERS["service_fee"]: "-9,75", HEADERS["vat"]: "0,00",
                        HEADERS["tax"]: "0,00", HEADERS["net_payable"]: "819,00"})
    payout = {field: "-" for field in fields}
    payout.update({HEADERS["record_type"]: "(Payout)", HEADERS["reference"]: "FAKE-PAYOUT-1",
                   HEADERS["issued_on"]: "05.09.2026", HEADERS["transaction_currency"]: "RON",
                   HEADERS["payout_currency"]: "RON", HEADERS["payout_value"]: "667,80",
                   HEADERS["payout_date"]: "05.09.2026"})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows([reservation, payout])
    return output.getvalue().encode()


class TelegramDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "draft-test.sqlite3"
        with closing(connect(self.db_path)) as db:
            db.execute("SELECT 1")

    def tearDown(self):
        self.temp.cleanup()

    def test_booking_document_requires_confirmation_and_duplicate_is_idempotent(self):
        content = booking_csv()
        token, summary, _ = analyze_document(77, "monthly.csv", content, now=1000)
        self.assertIn("Raport Booking", summary)
        with closing(connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM booking_entries").fetchone()[0], 0)
        self.assertIn("Import confirmat", finish_draft(self.db_path, token, 77, confirm=True, now=1001))
        token2, _, _ = analyze_document(77, "again.csv", content, now=1002)
        self.assertIn("deja importat", finish_draft(self.db_path, token2, 77, confirm=True, now=1003))
        with closing(connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM booking_entries").fetchone()[0], 1)

    def test_draft_cannot_be_confirmed_by_another_user_or_after_expiry(self):
        token, _, _ = analyze_document(77, "monthly.csv", booking_csv(), now=2000)
        self.assertIn("nu aparține", finish_draft(self.db_path, token, 88, confirm=True, now=2001))
        self.assertIn("a expirat", finish_draft(self.db_path, token, 77, confirm=True, now=2000 + 30 * 60))

    def test_cancel_does_not_import(self):
        token, _, _ = analyze_document(77, "monthly.csv", booking_csv(), now=3000)
        self.assertIn("Import anulat", finish_draft(self.db_path, token, 77, confirm=False, now=3001))
        with closing(connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)

    def test_unsupported_pdf_fails_without_staging(self):
        with self.assertRaisesRegex(ValueError, "nu poate fi citită în siguranță"):
            analyze_document(77, "statement.pdf", b"%PDF-fake", now=4000)

    def test_document_row_limit_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "5000 de rânduri"):
            _check_rows([None] * 5001)

    def test_booking_preview_never_combines_currencies(self):
        _, summary, _ = analyze_document(77, "mixed.csv", booking_csv_mixed_currencies(), now=4500)
        self.assertIn("15,00 lei", summary)
        self.assertIn("3,00 euro", summary)
        self.assertNotIn("3,00 lei", summary)

    def test_booking_preview_uses_requested_order_and_clear_conclusion(self):
        _, summary, details = analyze_document(77, "booking.csv", booking_csv_with_summary_totals(), now=4600)
        expected = [
            "- Total rezervări: 975,00 lei",
            "- Comisioane Booking: 146,25 lei",
            "- Alte taxe: 9,75 lei",
            "- Net de primit: 819,00 lei",
            "- Deja plătit de Booking: 667,80 lei",
            "- Rămas de primit: 151,20 lei",
        ]
        positions = [summary.index(line) for line in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Concluzie: Booking mai are de plătit: 151,20 lei.", summary)
        self.assertIn("nu sunt confirmate în bancă", summary)
        self.assertTrue(details.startswith("Detalii Booking\n"))
        self.assertIn("Booking indică o plată: 667,80 lei", details)

    def test_confirmed_booking_import_keeps_short_title_and_preview(self):
        token, _, _ = analyze_document(77, "booking.csv", booking_csv_with_summary_totals(), now=4700)
        answer = finish_draft(self.db_path, token, 77, confirm=True, now=4701)
        self.assertTrue(answer.startswith("Raport Booking\nImport confirmat:"))
        self.assertIn("- Rămas de primit: 151,20 lei", answer)
        self.assertEqual(answer.count("Raport Booking"), 1)

    def test_creasoft_csv_preview_notes_cash_and_period_limits(self):
        content = "Vendomat;Cantitate;Incasari totale (cash + non-cash - rest)\nOradea Era;3;45,00\n".encode()
        token, summary, _ = analyze_document(77, "creasoft.csv", content, now=5000)
        self.assertTrue(summary.startswith("Raport Creasoft\n"))
        self.assertIn("Vânzări totale: 45,00 lei", summary)
        self.assertIn("nu separă numerarul de plățile cu cardul", summary)
        self.assertIn("Concluzie:", summary)
        with closing(connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM creasoft_reports").fetchone()[0], 0)
        self.assertIn("Import confirmat", finish_draft(self.db_path, token, 77, confirm=True, now=5001))


if __name__ == "__main__":
    unittest.main()
