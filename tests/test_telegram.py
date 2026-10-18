import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from finance_store import Observation, connect, import_creasoft_report, import_observations
from telegram_bot import TelegramBotAPI, TelegramConfig, answer_message, set_webhook, verify_webhook_secret
from web_app import FinanceHTTPServer


OWNER_ID = 817263540
WEBHOOK_SECRET = "synthetic-webhook-secret-value-1234567890"


class FakeTelegramAPI:
    sent_text = []
    sent_pdfs = []

    def __init__(self, config):
        self.config = config

    def send_text(self, chat_id, text):
        self.sent_text.append((chat_id, text))

    def send_pdf(self, chat_id, path, caption):
        self.sent_pdfs.append((chat_id, Path(path).read_bytes(), caption))


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.payload


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "synthetic.sqlite3"
        with closing(connect(self.database)) as db:
            import_observations(db, "synthetic.csv", b"synthetic rows", [
                Observation("bank", "2026-09-02", "Synthetic income", "125.50", "RON",
                            "test", "income", "received", "SYNTH-IN", source_row=2),
                Observation("bank", "2026-09-03", "Synthetic expense", "-25.00", "RON",
                            "test", "expense", "received", "SYNTH-OUT", source_row=3),
            ])
        self.server = FinanceHTTPServer(self.database, 0)
        self.server.telegram_config = TelegramConfig(
            bot_token="synthetic-bot-token", webhook_secret=WEBHOOK_SECRET,
            allowed_user_ids=frozenset({OWNER_ID}), enable_messaging=True, allow_reports=False,
        )
        FakeTelegramAPI.sent_text = []
        FakeTelegramAPI.sent_pdfs = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def update(self, update_id=100, user_id=OWNER_ID, chat_type="private", text="Ce venituri am avut în 2026-09?"):
        return {
            "update_id": update_id,
            "message": {
                "message_id": 7,
                "from": {"id": user_id, "is_bot": False, "first_name": "Synthetic"},
                "chat": {"id": user_id, "type": chat_type},
                "date": int(time.time()), "text": text,
            },
        }

    def post(self, update, secret=WEBHOOK_SECRET):
        body = json.dumps(update, separators=(",", ":")).encode()
        request = Request(self.base + "/webhook/telegram", data=body, headers={
            "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": secret,
        })
        return urlopen(request)

    def test_secret_header_required_before_processing(self):
        with self.assertRaises(HTTPError) as error:
            self.post(self.update(), secret="wrong-secret")
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(FakeTelegramAPI.sent_text, [])

    def test_disabled_webhook_fails_closed(self):
        self.server.telegram_config = TelegramConfig(
            bot_token="", webhook_secret="", allowed_user_ids=frozenset(),
            enable_messaging=False, allow_reports=False,
        )
        with self.assertRaises(HTTPError) as error:
            self.post(self.update())
        self.assertEqual(error.exception.code, 503)

    def test_allowlisted_private_message_gets_aggregate_real_answer_from_synthetic_database(self):
        with patch("web_app.TelegramBotAPI", FakeTelegramAPI):
            response = self.post(self.update())
        self.assertEqual(response.status, 200)
        self.assertEqual(len(FakeTelegramAPI.sent_text), 1)
        chat_id, answer = FakeTelegramAPI.sent_text[0]
        self.assertEqual(chat_id, OWNER_ID)
        self.assertIn("125,50 lei", answer)
        self.assertIn("Venituri înregistrate", answer)
        self.assertNotIn("Synthetic income", answer)
        self.assertIn("Ultima actualizare a datelor:", answer)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM telegram_webhook_receipts WHERE status='completed'").fetchone()[0], 1)

    def test_non_allowlisted_user_and_group_never_receive_financial_data(self):
        with patch("web_app.TelegramBotAPI", FakeTelegramAPI):
            self.post(self.update(user_id=OWNER_ID + 1))
            self.post(self.update(update_id=101, chat_type="group"))
        self.assertEqual(FakeTelegramAPI.sent_text, [])

    def test_retry_of_same_update_sends_one_reply(self):
        with patch("web_app.TelegramBotAPI", FakeTelegramAPI):
            self.post(self.update(update_id=102))
            self.post(self.update(update_id=102))
        self.assertEqual(len(FakeTelegramAPI.sent_text), 1)

    def test_start_and_greeting_do_not_require_financial_records(self):
        with closing(connect(self.database)) as db:
            answer, report = answer_message(db, "Salut!", int(time.time()), False)
        self.assertIn("Salut!", answer)
        self.assertIsNone(report)

    def test_pdf_is_only_sent_when_explicitly_requested_and_enabled(self):
        self.server.telegram_config = TelegramConfig(**{
            **self.server.telegram_config.__dict__, "allow_reports": True,
        })
        with patch("web_app.TelegramBotAPI", FakeTelegramAPI):
            response = self.post(self.update(update_id=103, text="Trimite-mi raportul financiar lunar"))
        self.assertEqual(response.status, 200)
        self.assertEqual(FakeTelegramAPI.sent_text, [])
        self.assertEqual(len(FakeTelegramAPI.sent_pdfs), 1)
        chat_id, pdf, caption = FakeTelegramAPI.sent_pdfs[0]
        self.assertEqual(chat_id, OWNER_ID)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn("solicitat de tine", caption)

    def test_missing_machine_data_is_reported_without_inventing_a_balance(self):
        with closing(connect(self.database)) as db:
            answer, report = answer_message(db, "Câți bani am în primul tonomat?", int(time.time()), False)
        self.assertIn("Nu există date înregistrate", answer)
        self.assertIsNone(report)

    def test_available_money_keeps_vending_cash_unknown_and_booking_separate(self):
        with closing(connect(self.database)) as db:
            answer, report = answer_message(db, "Câți bani am acum?", int(time.time()), False)
        self.assertIsNone(report)
        self.assertIn("soldul nu a fost introdus manual", answer)
        self.assertIn("numerarul din aparate nu este cunoscut", answer)
        self.assertIn("sumele Booking sunt separate", answer)

    def test_era_and_crisul_names_route_to_their_synthetic_creasoft_reports(self):
        with closing(connect(self.database)) as db:
            import_creasoft_report(db, "era.csv", b"synthetic-era", {
                "Oradea Era": {"cantitate": 4, "incasari": 40.00},
            })
            era, _ = answer_message(db, "Cât a produs tonomatul de la ERA?", int(time.time()), False)
            import_creasoft_report(db, "crius.csv", b"synthetic-crius", {
                "Crișul Shopping Center — Oradea": {"cantitate": 3, "incasari": 27.00},
            })
            crius, _ = answer_message(db, "Cât a produs tonomatul de la Crișul?", int(time.time()), False)
        self.assertIn("40,00 lei", era)
        self.assertIn("soldul aparatului nu poate fi stabilit", era)
        self.assertIn("al doilea tonomat", crius)
        self.assertIn("27,00 lei", crius)

    def test_financial_answers_are_short_and_show_bank_confirmed_income(self):
        with closing(connect(self.database)) as db:
            answer, report = answer_message(db, "Cât am încasat?", int(time.time()), False)
        self.assertIsNone(report)
        self.assertTrue(answer.startswith("Raport financiar — "))
        self.assertIn("Venituri înregistrate:", answer)
        self.assertIn("Încasat confirmat în bancă:", answer)
        self.assertIn("Concluzie:", answer)

    def test_situation_report_answers_all_five_financial_questions(self):
        with closing(connect(self.database)) as db:
            answer, report = answer_message(db, "/situatie", int(time.time()), False)
        self.assertIsNone(report)
        for label in ("Bani disponibili:", "Încasat confirmat în bancă:", "Cheltuieli:",
                      "Profit estimat:", "De primit de la Booking:"):
            self.assertIn(label, answer)

    def test_webhook_secret_validation_and_config_are_fail_closed(self):
        self.assertTrue(verify_webhook_secret(self.server.telegram_config, WEBHOOK_SECRET))
        self.assertFalse(verify_webhook_secret(self.server.telegram_config, "bad"))
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(TelegramConfig.from_env().webhook_ready)

    def test_set_webhook_uses_https_root_path_and_only_message_updates(self):
        calls = []
        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            return FakeResponse({"ok": True, "result": True})
        with patch("telegram_bot.urllib.request.urlopen", side_effect=fake_urlopen):
            set_webhook(self.server.telegram_config, "https://finance.example/login")
        request, timeout = calls[0]
        self.assertTrue(request.full_url.endswith("/setWebhook"))
        self.assertEqual(timeout, 10)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        payload = json.loads(request.data)
        self.assertEqual(payload["url"], "https://finance.example/webhook/telegram")
        self.assertEqual(payload["allowed_updates"], ["message", "callback_query"])
        self.assertEqual(payload["secret_token"], WEBHOOK_SECRET)

    def test_startup_can_register_webhook_only_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "synthetic-bot-token",
            "TELEGRAM_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "TELEGRAM_ALLOWED_USER_IDS": str(OWNER_ID),
            "TELEGRAM_ENABLE_MESSAGING": "true",
            "TELEGRAM_CONFIGURE_WEBHOOK_ON_START": "true",
            "APP_PUBLIC_URL": "https://finance.example/login",
        }), patch("web_app.set_webhook") as register:
            server = FinanceHTTPServer(Path(folder) / "startup.sqlite3", 0)
            try:
                self.assertTrue(server.telegram_webhook_setup_ok)
                register.assert_called_once_with(server.telegram_config, "https://finance.example/login")
            finally:
                server.server_close()

    def test_bot_api_send_message_uses_bot_endpoint_and_json(self):
        calls = []
        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            return FakeResponse({"ok": True, "result": {"message_id": 2}})
        with patch("telegram_bot.urllib.request.urlopen", side_effect=fake_urlopen):
            TelegramBotAPI(self.server.telegram_config).send_text(OWNER_ID, "Synthetic answer")
        request, timeout = calls[0]
        self.assertTrue(request.full_url.endswith("/sendMessage"))
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")
        self.assertEqual(json.loads(request.data), {"chat_id": OWNER_ID, "text": "Synthetic answer"})
        self.assertEqual(timeout, 10)


if __name__ == "__main__":
    unittest.main()

