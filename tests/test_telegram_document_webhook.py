import csv
import io
import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

from booking_imports import HEADERS
from finance_store import connect
from telegram_bot import TelegramConfig
from web_app import FinanceHTTPServer

OWNER = 111222333
SECRET = "synthetic-secret-for-telegram-document-tests"


def booking_csv():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(HEADERS.values()))
    writer.writeheader()
    row = {field: "-" for field in HEADERS.values()}
    row.update({HEADERS["record_type"]: "Rezervare", HEADERS["reference"]: "FAKE-REF",
                HEADERS["issued_on"]: "01.09.2026", HEADERS["reservation_status"]: "OK",
                HEADERS["gross"]: "100,00", HEADERS["commission"]: "-15,00",
                HEADERS["transaction_value"]: "100,00", HEADERS["transaction_currency"]: "RON",
                HEADERS["net_payable"]: "85,00", HEADERS["payout_currency"]: "RON"})
    writer.writerow(row)
    return output.getvalue().encode()


class FakeBotAPI:
    document = booking_csv()
    downloaded = 0
    previews = []
    texts = []
    def __init__(self, config): self.config = config
    def download_document(self, file_id):
        type(self).downloaded += 1
        return "report.csv", type(self).document
    def send_document_preview(self, chat_id, text, token): type(self).previews.append((chat_id, text, token))
    def send_text(self, chat_id, text): type(self).texts.append((chat_id, text))
    def send_pdf(self, chat_id, path, caption): pass
    def answer_callback(self, callback_id): pass


class TelegramDocumentWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "ledger.sqlite3"
        with closing(connect(self.database)) as db: db.execute("SELECT 1")
        self.server = FinanceHTTPServer(self.database, 0)
        self.server.telegram_config = TelegramConfig("synthetic-token", SECRET,
            frozenset({OWNER}), True, False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        FakeBotAPI.downloaded = 0
        FakeBotAPI.previews = []
        FakeBotAPI.texts = []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def post(self, update):
        body = json.dumps(update).encode()
        request = Request(self.base + "/webhook/telegram", data=body, headers={
            "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET})
        return urlopen(request)

    def doc_update(self, update_id, user_id=OWNER, name="monthly.csv"):
        return {"update_id": update_id, "message": {"from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "document": {"file_id": "synthetic-file-id", "file_name": name, "file_size": len(FakeBotAPI.document)}}}

    def test_preview_then_confirm_imports_once(self):
        with patch("web_app.TelegramBotAPI", FakeBotAPI):
            self.assertEqual(self.post(self.doc_update(1)).status, 200)
            self.assertEqual(len(FakeBotAPI.previews), 1)
            with closing(connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM booking_entries").fetchone()[0], 0)
            token = FakeBotAPI.previews[0][2]
            callback = {"update_id": 2, "callback_query": {"id": "callback-1", "from": {"id": OWNER},
                "message": {"chat": {"id": OWNER, "type": "private"}}, "data": f"imp:{token}:y"}}
            self.assertEqual(self.post(callback).status, 200)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM booking_entries").fetchone()[0], 1)

    def test_unauthorized_user_is_rejected_before_file_download(self):
        with patch("web_app.TelegramBotAPI", FakeBotAPI):
            self.assertEqual(self.post(self.doc_update(3, user_id=999888777)).status, 200)
        self.assertEqual(FakeBotAPI.downloaded, 0)
        self.assertEqual(FakeBotAPI.previews, [])

    def test_unsupported_pdf_is_explained_without_import(self):
        with patch("web_app.TelegramBotAPI", FakeBotAPI):
            self.post(self.doc_update(4, name="statement.pdf"))
        self.assertIn("nu poate fi citită în siguranță", FakeBotAPI.texts[-1][1])
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
