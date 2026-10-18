import base64
import csv
import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from booking_imports import HEADERS
from finance_store import connect
from gmail_booking import GmailBookingConfig, _booking_sender, sync_booking_mail


def booking_csv() -> bytes:
    output = io.StringIO(newline="")
    fields = list(HEADERS.values())
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    row = {field: "-" for field in fields}
    row.update({
        HEADERS["record_type"]: "Rezervare",
        HEADERS["reference"]: "FAKE-REFERENCE-ONLY",
        HEADERS["issued_on"]: "01.09.2026",
        HEADERS["check_in"]: "10.09.2026",
        HEADERS["check_out"]: "12.09.2026",
        HEADERS["reservation_status"]: "OK",
        HEADERS["gross"]: "100,00",
        HEADERS["commission"]: "-15,00",
        HEADERS["service_fee"]: "-1,00",
        HEADERS["vat"]: "0,00",
        HEADERS["transaction_value"]: "100,00",
        HEADERS["transaction_currency"]: "RON",
        HEADERS["net_payable"]: "84,00",
        HEADERS["payout_currency"]: "RON",
    })
    writer.writerow(row)
    return output.getvalue().encode("utf-8")


class GmailBookingTests(unittest.TestCase):
    def test_only_booking_sender_domains_are_accepted(self):
        self.assertTrue(_booking_sender({"payload": {"headers": [
            {"name": "From", "value": "Booking Reports <reports@booking.com>"}]}}))
        self.assertTrue(_booking_sender({"payload": {"headers": [
            {"name": "From", "value": "reports@mail.booking.com"}]}}))
        self.assertFalse(_booking_sender({"payload": {"headers": [
            {"name": "From", "value": "booking.com.attacker.example@attacker.example"}]}}))

    def test_import_is_idempotent_and_keeps_no_email_subject_or_reference(self):
        content = booking_csv()
        encoded = base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")

        def api(url, token):
            if "/messages?" in url:
                return {"messages": [{"id": "fake-message-id"}]}
            if "format=metadata" in url:
                return {"payload": {"headers": [{"name": "From", "value": "reports@booking.com"}]}}
            if "format=full" in url:
                return {"payload": {"parts": [{
                    "filename": "fake-monthly-report.csv", "mimeType": "text/csv",
                    "body": {"attachmentId": "fake-attachment-id", "size": len(content)},
                }]}}
            if "/attachments/" in url:
                return {"data": encoded}
            raise AssertionError("Unexpected API endpoint")

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            config = GmailBookingConfig("fake-client", "fake-secret", "fake-refresh")
            with patch("gmail_booking._access_token", return_value="fake-access"), patch("gmail_booking._api_json", side_effect=api):
                result = sync_booking_mail(db_path, config)
                again = sync_booking_mail(db_path, config)
            self.assertEqual(result, {"messages_scanned": 1, "attachments_imported": 1, "records_imported": 1})
            self.assertEqual(again, {"messages_scanned": 0, "attachments_imported": 0, "records_imported": 0})
            with closing(connect(db_path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM booking_entries").fetchone()[0], 1)
                stored = " ".join(str(value) for value in db.execute("SELECT filename,sha256 FROM imports").fetchone())
                self.assertNotIn("fake-monthly-report.csv", stored)
                self.assertNotIn("FAKE-REFERENCE-ONLY", json.dumps([dict(row) for row in db.execute("SELECT * FROM booking_entries")]))
                self.assertEqual(db.execute("SELECT count(*) FROM gmail_booking_messages").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

