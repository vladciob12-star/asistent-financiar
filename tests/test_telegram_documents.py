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

    def test_creasoft_csv_preview_notes_cash_and_period_limits(self):
        content = "Vendomat;Cantitate;Incasari totale (cash + non-cash - rest)\nOradea Era;3;45,00\n".encode()
        token, summary, _ = analyze_document(77, "creasoft.csv", content, now=5000)
        self.assertIn("Numerarul și plățile non-cash sunt combinate", summary)
        with closing(connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM creasoft_reports").fetchone()[0], 0)
        self.assertIn("Import confirmat", finish_draft(self.db_path, token, 77, confirm=True, now=5001))


if __name__ == "__main__":
    unittest.main()
