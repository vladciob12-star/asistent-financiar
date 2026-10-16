import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from finance_store import (Observation, classify_observation, connect,
                           confirm_reconciliation, import_creasoft_report, import_observations,
                           monthly_summary, write_html_report)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "ledger.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_reimport_same_file_is_idempotent(self):
        row = Observation("bank", "2026-09-01", "Depunere", "10.00", "RON", "personal", "income", "received", "A1", source_row=2)
        args=("statement.csv", b"sample bytes only", [row])
        _, count = import_observations(self.db, *args)
        _, repeated = import_observations(self.db, *args)
        self.assertEqual((count, repeated), (1, 0))
        self.assertEqual(self.db.execute("select count(*) from observations").fetchone()[0], 1)

    def test_creasoft_aggregates_keep_period_and_cash_uncertainty_explicit(self):
        totals = {
            "Oradea Era": {"cantitate": 10, "incasari": 125.50},
            "Crișul Shopping Center — Oradea": {"cantitate": 8, "incasari": 99.25},
        }
        _, count = import_creasoft_report(self.db, "creasoft.csv", b"synthetic report", totals)
        _, repeated = import_creasoft_report(self.db, "creasoft.csv", b"synthetic report", totals)
        self.assertEqual((count, repeated), (2, 0))
        rows = self.db.execute("SELECT activity,period_start,period_end,cash_status FROM creasoft_reports ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in rows], ["vending_crius_shopping_center", "vending_oradea_era"])
        self.assertTrue(all(row[1] is None and row[2] is None for row in rows))
        self.assertTrue(all(row[3] == "cash_and_non_cash_combined" for row in rows))
        summary = monthly_summary(self.db, "2026-09")
        self.assertEqual(len(summary["creasoft_reports_without_period"]), 2)
        self.assertEqual(summary["activities"], {})

    def test_unknown_creasoft_machine_is_retained_for_review(self):
        _, count = import_creasoft_report(self.db, "creasoft.csv", b"unknown synthetic report", {
            "Automat Mystery": {"cantitate": 1, "incasari": 2.50},
        })
        self.assertEqual(count, 1)
        row = self.db.execute("SELECT machine_label,activity,review_status FROM creasoft_reports").fetchone()
        self.assertEqual(tuple(row), ("Automat Mystery", "unassigned", "needs_review"))

    def test_cli_imports_creasoft_report_to_sqlite(self):
        report = Path(self.tmp.name) / "creasoft.csv"
        report.write_text("Vendomat;Cantitate;Incasari totale (cash + non-cash - rest)\nOradea Era;2;23,50\n",
                          encoding="utf-8")
        database = Path(self.tmp.name) / "creasoft.sqlite3"
        main = Path(__file__).resolve().parents[1] / "main.py"
        result = subprocess.run([sys.executable, str(main), "--import-creasoft-report", str(report),
                                 "--database", str(database)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        check = connect(database)
        try:
            row = check.execute("SELECT activity,reported_sales,period_start FROM creasoft_reports").fetchone()
            self.assertEqual(tuple(row), ("vending_oradea_era", "23.50", None))
        finally:
            check.close()

    def test_booking_receivable_is_not_bank_funds_or_received_income(self):
        booking=Observation("booking","2026-09-03","Payout scheduled","500.00","RON","booking_property","income","scheduled","B1",source_row=2)
        bank=Observation("bank","2026-09-03","Booking payout","500.00","RON","booking_property","income","received","T1",source_row=2)
        import_observations(self.db,"booking.csv",b"booking",[booking])
        import_observations(self.db,"bank.csv",b"bank",[bank])
        result=monthly_summary(self.db,"2026-09")
        self.assertEqual(result["booking_receivables_not_cash"],[{"currency":"RON","amount":"500.00"}])
        self.assertEqual(result["bank_received_totals"],[{"currency":"RON","amount":"500.00"}])
        self.assertEqual(result["activities"]["booking_property"]["RON"]["income"],"500.00")
        self.assertEqual(len(result["reconciliation_issues"]),1)
        confirm_reconciliation(self.db, result["reconciliation_issues"][0]["id"])
        self.assertEqual(monthly_summary(self.db,"2026-09")["reconciliation_issues"],[])

    def test_suspected_duplicate_is_retained_and_marked(self):
        first=Observation("bank","2026-09-01","Payout","10.00","RON","x","income","received","ID1",source_row=2)
        second=Observation("bank","2026-09-01","Same payout","10.00","RON","x","income","received","ID1",source_row=2)
        import_observations(self.db,"one.csv",b"one",[first])
        import_observations(self.db,"two.csv",b"two",[second])
        rows=self.db.execute("select duplicate_of from observations order by id").fetchall()
        self.assertIsNone(rows[0][0])
        self.assertEqual(rows[1][0],1)
        self.assertEqual(monthly_summary(self.db,"2026-09")["activities"]["x"]["RON"]["income"],"10.00")

    def test_manual_expense_classification_uses_absolute_expense_amount(self):
        row=Observation("bank","2026-09-01","Comision","-0.10","RON",source_row=2)
        import_observations(self.db,"fees.csv",b"fees",[row])
        before=monthly_summary(self.db,"2026-09")
        self.assertEqual(before["unclassified"][0]["description"],"Comision")
        self.assertEqual(before["activities"],{})
        classify_observation(self.db,1,activity="bank",kind="commission")
        after=monthly_summary(self.db,"2026-09")
        self.assertEqual(after["activities"]["bank"]["RON"]["commission"],"0.10")
        self.assertEqual(after["activities"]["bank"]["RON"]["estimated_result"],"-0.10")

    def test_monthly_activities_keep_currencies_separate(self):
        ron=Observation("bank","2026-09-01","RON income","100.00","RON","shop","income","received","R1",source_row=2)
        eur=Observation("bank","2026-09-02","EUR expense","-25.00","EUR","shop","expense","recorded","E1",source_row=2)
        import_observations(self.db,"ron.csv",b"ron",[ron])
        import_observations(self.db,"eur.csv",b"eur",[eur])
        activity=monthly_summary(self.db,"2026-09")["activities"]["shop"]
        self.assertEqual(activity["RON"]["income"],"100.00")
        self.assertEqual(activity["RON"]["estimated_result"],"100.00")
        self.assertEqual(activity["EUR"]["expense"],"25.00")
        self.assertEqual(activity["EUR"]["estimated_result"],"-25.00")

    def test_invalid_batch_rolls_back_everything(self):
        valid=Observation("bank","2026-09-01","A","2.00","RON",source_row=2)
        invalid=Observation("bank","bad date","B","3.00","RON",source_row=3)
        with self.assertRaises(ValueError):
            import_observations(self.db,"bad.csv",b"bad",[valid,invalid])
        self.assertEqual(self.db.execute("select count(*) from imports").fetchone()[0],0)

    def test_report_html_escapes_imported_content(self):
        row=Observation("bank","2026-09-01","<script>alert(1)</script>","1.00","RON",source_row=2)
        import_observations(self.db,"evil.csv",b"html",[row])
        out=Path(self.tmp.name)/"report.html"
        write_html_report(self.db,"2026-09",out)
        content=out.read_text(encoding="utf-8")
        self.assertNotIn("<script>",content)
        self.assertIn("&lt;script&gt;",content)

    def test_cli_generates_html_report_from_sqlite_ledger(self):
        row=Observation("bank","2026-09-01","Supplier", "-12.34", "RON", "business", "expense", "recorded", source_row=2)
        import_observations(self.db,"statement.csv",b"monthly-report",[row])
        database=Path(self.tmp.name)/"ledger.sqlite3"
        # Close the in-memory handle before the subprocess opens the same file.
        self.db.close()
        main=Path(__file__).resolve().parents[1]/"main.py"
        report=Path(self.tmp.name)/"report.html"
        result=subprocess.run([sys.executable,str(main),"--report-month","2026-09","--database",str(database),
                               "--report-html",str(report)],capture_output=True,text=True,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("12.34",report.read_text(encoding="utf-8"))
        self.assertIn("Monedă",report.read_text(encoding="utf-8"))
        check=connect(database)
        try:
            self.assertEqual(check.execute("select period from reports").fetchone()[0],"2026-09")
        finally:
            check.close()


if __name__ == "__main__":
    unittest.main()
