import json
import re
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor, HTTPRedirectHandler
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from urllib.parse import urlencode

from booking_imports import HEADERS
from finance_store import connect
from web_app import FinanceHTTPServer, hash_password


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
        self.assertIn("Venituri clasificate în lună", self.page)
        self.assertIn("Cheltuieli și comisioane", self.page)
        self.assertIn("Profit estimat", self.page)
        self.assertIn("Bani disponibili", self.page)
        self.assertIn("/report.pdf?month=", self.page)

    def test_manual_balance_and_monthly_pdf_download(self):
        page = self.post("/bank-balance", {
            "csrf": self.csrf, "month": "2026-09", "amount": "2500.00",
            "currency": "RON", "as_of": "2026-09-30",
        })
        self.assertIn("Soldul bancar introdus manual a fost salvat", page)
        with closing(connect(self.database)) as db:
            self.assertEqual(tuple(db.execute("SELECT currency,amount,as_of FROM bank_balances").fetchone()),
                             ("RON", "2500.00", "2026-09-30"))
        response = urlopen(self.base + "/report.pdf?month=2026-09")
        self.assertEqual(response.headers.get_content_type(), "application/pdf")
        self.assertTrue(response.read().startswith(b"%PDF-"))

    def test_manual_bank_import_keeps_file_temporary_and_stores_mapped_fields(self):
        mapping = {
            "source": "bank", "currency": "RON", "date_format": "%Y-%m-%d", "delimiter": ",",
            "columns": {"date": "Date", "description": "Description", "amount": "Amount", "external_id": "Reference"},
        }
        content = b"Date,Description,Amount,Reference\n2026-09-15,SYNTHETIC PAYMENT,10.00,SYNTHETIC-ID\n"
        page = self.post("/import/bank", {
            "csrf": self.csrf, "month": "2026-09", "mapping": json.dumps(mapping),
        }, "synthetic-statement.csv", content)
        self.assertIn("Extras bancar: importate 1 tranzacții", page)
        with closing(connect(self.database)) as db:
            row = db.execute("SELECT description,external_id,raw_json FROM observations").fetchone()
            self.assertEqual(row[0:2], ("SYNTHETIC PAYMENT", "SYNTHETIC-ID"))
            self.assertEqual(row["raw_json"], "{}")
            self.assertEqual(db.execute("SELECT filename FROM imports").fetchone()[0], "synthetic-statement.csv")

    def test_cloud_auth_requires_https_hash_and_gates_dashboard(self):
        with self.assertRaises(ValueError):
            FinanceHTTPServer(self.database, 0, host="0.0.0.0")
        encoded_hash, salt = hash_password("Synthetic-Only-Cloud-Password")
        self.server.auth_password_hash = encoded_hash
        self.server.password_salt = salt
        self.server.public_origin = "https://finance.example"
        self.server.secure_cookies = True
        jar = CookieJar()
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = build_opener(HTTPCookieProcessor(jar), NoRedirect)
        login_page = opener.open(self.base + "/login").read().decode()
        self.assertIn("Autentificare", login_page)
        token = re.search(r'name="csrf" value="([^"]+)"', login_page).group(1)
        page = self.post("/import/creasoft", {
            "csrf": token, "month": "2026-09",
        }, "synthetic.csv", b"not-used")
        self.assertIn("Autentificare", page)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)

        body = urlencode({"csrf": token, "password": "Synthetic-Only-Cloud-Password"}).encode("utf-8")
        request = Request(self.base + "/login", data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://finance.example",
        })
        try:
            opener.open(request)
        except HTTPError as response:
            self.assertEqual(response.code, 303)
            cookie_header = response.headers.get("Set-Cookie", "")
        else:
            self.fail("Login-ul trebuia să redirecționeze către tabloul de bord.")
        self.assertIn("; Secure", cookie_header)
        session = re.search(r"finance_session=([^;]+)", cookie_header).group(1)
        logged_in = urlopen(Request(self.base + "/", headers={"Cookie": f"finance_session={session}"})).read().decode()
        self.assertIn("Asistent Financiar Personal", logged_in)


    def test_database_session_survives_a_server_restart(self):
        encoded_hash, salt = hash_password("Synthetic-Only-Cloud-Password")
        old_server, old_thread = self.server, self.thread
        port = old_server.server_port
        old_server.shutdown(); old_server.server_close(); old_thread.join(timeout=2)
        self.server = FinanceHTTPServer(
            self.database, port, password_hash=encoded_hash, password_salt=salt,
            public_origin="", secure_cookies=False,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        login_page = opener.open(self.base + "/login").read().decode("utf-8")
        token = re.search(r'name="csrf" value="([^"]+)"', login_page).group(1)
        body = urlencode({"csrf": token, "password": "Synthetic-Only-Cloud-Password"}).encode("utf-8")
        request = Request(self.base + "/login", data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", "Origin": self.base,
        })
        self.assertIn("Asistent Financiar Personal", opener.open(request).read().decode("utf-8"))
        with closing(connect(self.database)) as db:
            stored = db.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
        session = next(cookie.value for cookie in jar if cookie.name == "finance_session")
        self.assertNotEqual(stored, session)
        old_server, old_thread = self.server, self.thread
        old_server.shutdown(); old_server.server_close(); old_thread.join(timeout=2)
        self.server = FinanceHTTPServer(
            self.database, port, password_hash=encoded_hash, password_salt=salt,
            public_origin="", secure_cookies=False,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.assertIn("Asistent Financiar Personal", opener.open(self.base + "/").read().decode("utf-8"))

    def test_login_origin_error_is_shown_on_login_page(self):
        encoded_hash, salt = hash_password("Synthetic-Only-Cloud-Password")
        self.server.auth_password_hash = encoded_hash
        self.server.password_salt = salt
        self.server.public_origin = "https://finance.example/login"
        login_page = urlopen(self.base + "/login").read().decode("utf-8")
        token = re.search(r'name="csrf" value="([^"]+)"', login_page).group(1)
        body = urlencode({"csrf": token, "password": "Synthetic-Only-Cloud-Password"}).encode("utf-8")
        request = Request(self.base + "/login", data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://wrong-origin.example",
        })
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)
        self.assertIn("Cererea externă a fost respinsă", error.exception.read().decode("utf-8"))

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
