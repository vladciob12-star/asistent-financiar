import csv
import io
import tempfile
import unittest
from pathlib import Path

from booking_imports import HEADERS, import_booking_csv
from finance_store import (Observation, connect, confirm_booking_reconciliation,
                           import_booking_records, import_observations, monthly_summary)


def booking_csv() -> bytes:
    columns=list(HEADERS.values())
    output=io.StringIO(newline="")
    writer=csv.DictWriter(output,fieldnames=columns)
    writer.writeheader()
    reservation={header:"-" for header in columns}
    reservation.update({HEADERS["record_type"]:"Rezervare",HEADERS["reference"]:"SENSITIVE-BOOKING-REF",
        HEADERS["issued_on"]:"01.09.2026",HEADERS["check_in"]:"10.09.2026",HEADERS["check_out"]:"12.09.2026",
        HEADERS["reservation_status"]:"OK",HEADERS["gross"]:"100,00",HEADERS["commission"]:"-15,00",
        HEADERS["service_fee"]:"-1,00",HEADERS["vat"]:"0,00",HEADERS["tax"]:"-",
        HEADERS["transaction_value"]:"100,00",HEADERS["transaction_currency"]:"RON",
        HEADERS["net_payable"]:"84,00",HEADERS["payout_value"]:"-",
        HEADERS["payout_currency"]:"RON",HEADERS["payout_date"]:"-"})
    payout={header:"-" for header in columns}
    payout.update({HEADERS["record_type"]:"(Payout)",HEADERS["reference"]:"",
        HEADERS["issued_on"]:"05.09.2026",HEADERS["reservation_status"]:"-",
        HEADERS["transaction_currency"]:"RON",HEADERS["payout_currency"]:"RON",
        HEADERS["payout_value"]:"84,00",HEADERS["payout_date"]:"05.09.2026"})
    writer.writerow(reservation)
    writer.writerow(payout)
    return output.getvalue().encode("utf-8")


class BookingReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/"booking.csv"
        self.content=booking_csv()
        self.path.write_bytes(self.content)
        self.db=connect(self.root/"ledger.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_separate_booking_amounts_and_hash_reference(self):
        records=import_booking_csv(self.path)
        reservation,payout=records
        self.assertEqual(reservation.gross,"100.00")
        self.assertEqual(reservation.commission,"-15.00")
        self.assertEqual(reservation.net_payable,"84.00")
        self.assertNotEqual(reservation.reference_hash,"SENSITIVE-BOOKING-REF")
        import_booking_records(self.db,self.path.name,self.content,records)
        summary=monthly_summary(self.db,"2026-09")
        self.assertEqual(summary["booking_reported_payouts_not_bank_confirmed"],
                         [{"currency":"RON","amount":"84.00"}])
        self.assertEqual(summary["bank_received_totals"],[])
        self.assertEqual(self.db.execute("select reference_hash from booking_entries where record_type='Rezervare'").fetchone()[0],reservation.reference_hash)
        self.assertNotIn("SENSITIVE-BOOKING-REF",self.db.execute("select reference_hash from booking_entries").fetchone()[0])

    def test_exact_duplicate_file_is_idempotent(self):
        records=import_booking_csv(self.path)
        self.assertEqual(import_booking_records(self.db,self.path.name,self.content,records)[1],2)
        self.assertEqual(import_booking_records(self.db,self.path.name,self.content,records)[1],0)
        self.assertEqual(self.db.execute("select count(*) from booking_entries").fetchone()[0],2)

    def test_bank_match_is_only_a_proposal_until_manually_confirmed(self):
        bank=Observation("bank","2026-09-05","Booking settlement","84.00","RON",
                         "booking_property","income","received","BANK-1",source_row=2)
        import_observations(self.db,"bank.csv",b"synthetic bank",[bank])
        records=import_booking_csv(self.path)
        import_booking_records(self.db,self.path.name,self.content,records)
        summary=monthly_summary(self.db,"2026-09")
        self.assertEqual(len(summary["reconciliation_issues"]),1)
        self.assertEqual(summary["booking_reported_payouts_not_bank_confirmed"],
                         [{"currency":"RON","amount":"84.00"}])
        confirm_booking_reconciliation(self.db,summary["reconciliation_issues"][0]["id"])
        after=monthly_summary(self.db,"2026-09")
        self.assertEqual(after["reconciliation_issues"],[])
        self.assertEqual(after["booking_reported_payouts_not_bank_confirmed"],[])
        self.assertEqual(after["bank_received_totals"],[{"currency":"RON","amount":"84.00"}])

    def test_invalid_booking_headers_fail_closed(self):
        bad=self.root/"bad.csv"
        bad.write_text("amount,date\n1,2026-09-01\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"lipsesc coloanele Booking"):
            import_booking_csv(bad)


if __name__ == "__main__":
    unittest.main()
