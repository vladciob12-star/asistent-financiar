import json
import re
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from booking_imports import HEADERS
from finance_store import connect
from web_app import FinanceHTTPServer


def multipart(fields, filename=None, content=None):
    boundary = "----finance-app-test-boundary"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    if filename is not None:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "ledger.sqlite3"
        self.server = FinanceHTTPServer(self.database, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        response = urlopen(self.base)
        self.page = response.read().decode("utf-8")
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', self.page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def post(self, path, fields, filename=None, content=None):
        boundary, body = multipart(fields, filename, content)
        req = Request(self.base + path, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": self.base,
        })
        return urlopen(req).read().decode("utf-8")

    def test_dashboard_is_local_and_has_all_three_import_forms(self):
        self.assertIn("Aplicație locală", self.page)
        self.assertIn("/import/creasoft", self.page)
        self.assertIn("/import/booking", self.page)
        self.assertIn("/import/bank", self.page)
        self.assertIn("Potriviri propuse", self.page)

    def test_creasoft_report_upload_is_saved_without_inventing_period(self):
        content = (
            "Vendomat;Cantitate;Incasari totale (cash + non-cash - rest)\n"
            "Oradea Era;3;45,50\n"
        ).encode("utf-8")
        page = self.post("/import/creasoft", {"csrf": self.csrf, "month": "2026-09"}, "sales.csv", content)
        self.assertIn("Creasoft: importate 1 agregate", page)
        with closing(connect(self.database)) as db:
            record = db.execute("SELECT reported_sales,period_start,cash_status FROM creasoft_reports").fetchone()
            self.assertEqual(tuple(record), ("45.50", None, "cash_and_non_cash_combined"))

    def test_booking_then_bank_import_proposes_and_confirms_reconciliation(self):
        values = {
            "record_type": "(Payout)", "reference": "SYNTHETIC-BOOKING-REF",
            "issued_on": "02.09.2026", "check_in": "", "check_out": "", "reservation_status": "",
            "gross": "", "commission": "", "service_fee": "", "vat": "", "tax": "",
            "transaction_value": "", "transaction_currency": "EUR", "net_payable": "500.00",
            "payout_value": "500.00", "payout_currency": "EUR", "payout_date": "02.09.2026",
        }
        booking_csv = (";".join(HEADERS[key] for key in values) + "\n" +
                       ";".join(values[key] for key in values) + "\n").encode("utf-8")
        page = self.post("/import/booking", {"csrf": self.csrf, "month": "2026-09"}, "booking.csv", booking_csv)
        self.assertIn("Booking: importate 1 rânduri", page)

        mapping = {
            "source": "bank", "currency": "EUR", "date_format": "%d.%m.%Y", "delimiter": ";",
            "columns": {"date": "Data", "description": "Detalii", "amount": "Suma", "external_id": "ID"},
        }
        bank_csv = "Data;Detalii;Suma;ID\n02.09.2026;Payout Booking;500.0;BT-SYNTHETIC-1\n".encode("utf-8")
        page = self.post("/import/bank", {
            "csrf": self.csrf, "month": "2026-09", "mapping": json.dumps(mapping),
        }, "statement.csv", bank_csv)
        self.assertIn("Booking cu banca", page)
        self.assertIn("Confirmă încasarea", page)

        with closing(connect(self.database)) as db:
            reconciliation_id = db.execute("SELECT id FROM booking_reconciliations WHERE status='needs_review'").fetchone()[0]
        page = self.post("/confirm/booking", {
            "csrf": self.csrf, "month": "2026-09", "reconciliation_id": str(reconciliation_id),
        })
        self.assertIn("Încasarea Booking a fost confirmată", page)
        with closing(connect(self.database)) as db:
            status = db.execute("SELECT settlement_status FROM booking_entries").fetchone()[0]
            self.assertEqual(status, "received")
            self.assertEqual(db.execute("SELECT count(*) FROM booking_reconciliations WHERE status='confirmed'").fetchone()[0], 1)

    def test_invalid_csrf_does_not_write_an_import(self):
        content = b"Vendomat;Cantitate;Incasari totale (cash + non-cash - rest)\nOradea Era;1;1,00\n"
        page = self.post("/import/creasoft", {"csrf": "wrong", "month": "2026-09"}, "x.csv", content)
        self.assertIn("Token de formular invalid", page)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
