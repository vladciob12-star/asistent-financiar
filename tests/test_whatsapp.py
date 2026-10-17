import hashlib
import hmac
import json
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from finance_store import Observation, connect, import_observations
from web_app import FinanceHTTPServer
from whatsapp import MetaCloudAPI, WhatsAppConfig, financial_answer, verify_signature


APP_SECRET = "synthetic-meta-secret-for-tests"
OWNER = "40700000000"


class FakeMetaAPI:
    sent_text = []
    sent_pdfs = []

    def __init__(self, config):
        self.config = config

    def send_text(self, recipient, text):
        self.sent_text.append((recipient, text))

    def send_pdf(self, recipient, path, caption):
        self.sent_pdfs.append((recipient, Path(path).read_bytes(), caption))


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.payload


class WhatsAppTests(unittest.TestCase):
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
        self.server.auth_password_hash = "pbkdf2_sha256$600000$" + "0" * 64
        self.server.password_salt = "0" * 32
        self.server.public_origin = "https://finance.example.test"
        self.server.secure_cookies = True
        self.config = WhatsAppConfig(
            verify_token="synthetic-verify-token", app_secret=APP_SECRET,
            access_token="synthetic-access-token", phone_number_id="123456789",
            api_version="v23.0", allowed_senders=frozenset({OWNER}),
            enable_messaging=True, allow_reports=False,
        )
        self.server.whatsapp_config = self.config
        FakeMetaAPI.sent_text = []
        FakeMetaAPI.sent_pdfs = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def payload(self, message_id="wamid.SYNTHETIC-1", sender=OWNER,
                text="Ce venituri am avut în 2026-09?", timestamp=None, msg_type="text"):
        return {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [{
                "id": message_id, "from": sender, "timestamp": str(timestamp or int(time.time())),
                "type": msg_type, "text": {"body": text},
            }]}}]}],
        }

    def post_payload(self, payload, signature_secret=APP_SECRET):
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(signature_secret.encode(), body, hashlib.sha256).hexdigest()
        request = Request(self.base + "/webhook/whatsapp", data=body,
                          headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})
        return urlopen(request)

    def test_meta_get_verification_echoes_challenge_only_for_matching_token(self):
        response = urlopen(self.base + "/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=synthetic-verify-token&hub.challenge=123456")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b"123456")
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=123456")
        self.assertEqual(error.exception.code, 403)

    def test_invalid_hmac_is_rejected_before_message_processing(self):
        with self.assertRaises(HTTPError) as error:
            self.post_payload(self.payload(), signature_secret="wrong-secret")
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(FakeMetaAPI.sent_text, [])

    def test_allowlisted_question_uses_aggregate_synthetic_financial_values(self):
        with patch("web_app.MetaCloudAPI", FakeMetaAPI):
            response = self.post_payload(self.payload())
        self.assertEqual(response.status, 200)
        self.assertEqual(len(FakeMetaAPI.sent_text), 1)
        recipient, answer = FakeMetaAPI.sent_text[0]
        self.assertEqual(recipient, OWNER)
        self.assertIn("125.50 RON", answer)
        self.assertIn("Venituri clasificate pentru 2026-09", answer)
        self.assertNotIn("Synthetic income", answer)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM whatsapp_webhook_receipts WHERE status='completed'").fetchone()[0], 1)

    def test_unlisted_sender_never_receives_financial_data(self):
        with patch("web_app.MetaCloudAPI", FakeMetaAPI):
            self.assertEqual(self.post_payload(self.payload(sender="40709999999")).status, 200)
        self.assertEqual(FakeMetaAPI.sent_text, [])

    def test_meta_retry_of_same_message_sends_one_reply(self):
        with patch("web_app.MetaCloudAPI", FakeMetaAPI):
            self.post_payload(self.payload(message_id="wamid.SYNTHETIC-DUP"))
            self.post_payload(self.payload(message_id="wamid.SYNTHETIC-DUP"))
        self.assertEqual(len(FakeMetaAPI.sent_text), 1)

    def test_non_text_media_is_ignored_and_cannot_import_a_bank_statement(self):
        with patch("web_app.MetaCloudAPI", FakeMetaAPI):
            self.post_payload(self.payload(message_id="wamid.SYNTHETIC-MEDIA", msg_type="document"))
        self.assertEqual(FakeMetaAPI.sent_text, [])
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 1)

    def test_owner_requested_pdf_is_generated_and_sent_only_when_explicitly_enabled(self):
        self.server.whatsapp_config = WhatsAppConfig(**{
            **self.config.__dict__, "allow_reports": True,
        })
        with patch("web_app.MetaCloudAPI", FakeMetaAPI):
            response = self.post_payload(self.payload(
                message_id="wamid.SYNTHETIC-REPORT", text="/raport 2026-09"))
        self.assertEqual(response.status, 200)
        self.assertEqual(FakeMetaAPI.sent_text, [])
        self.assertEqual(len(FakeMetaAPI.sent_pdfs), 1)
        recipient, pdf_bytes, caption = FakeMetaAPI.sent_pdfs[0]
        self.assertEqual(recipient, OWNER)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertIn("2026-09", caption)

    def test_old_or_future_message_timestamp_is_not_replied_to(self):
        old = int(time.time()) - 25 * 3600
        with patch("web_app.MetaCloudAPI", FakeMetaAPI):
            self.post_payload(self.payload(message_id="wamid.SYNTHETIC-OLD", timestamp=old))
        self.assertEqual(FakeMetaAPI.sent_text, [])

    def test_signature_helper_uses_exact_raw_body(self):
        raw = b'{"synthetic":true}'
        signature = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(APP_SECRET, raw, signature))
        self.assertFalse(verify_signature(APP_SECRET, raw + b" ", signature))

    def test_meta_text_client_uses_configured_graph_api_and_bearer_token(self):
        captured = []
        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse({"messages": [{"id": "wamid.SYNTHETIC-OUT"}]})
        with patch("whatsapp.urllib.request.urlopen", side_effect=fake_urlopen):
            MetaCloudAPI(self.config).send_text(OWNER, "Valori fictive")
        request, timeout = captured[0]
        self.assertEqual(request.full_url, "https://graph.facebook.com/v23.0/123456789/messages")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-access-token")
        self.assertEqual(timeout, 8)
        self.assertEqual(json.loads(request.data)["to"], OWNER)

    def test_meta_pdf_client_uploads_document_then_sends_media_id(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "synthetic-report.pdf"
            pdf.write_bytes(b"%PDF-synthetic-report")
            calls = []
            responses = iter([FakeResponse({"id": "MEDIA-SYNTHETIC"}),
                              FakeResponse({"messages": [{"id": "wamid.SYNTHETIC-PDF"}]})])
            def fake_urlopen(request, timeout):
                calls.append(request)
                return next(responses)
            with patch("whatsapp.urllib.request.urlopen", side_effect=fake_urlopen):
                MetaCloudAPI(self.config).send_pdf(OWNER, pdf, "Raport fictiv")
        self.assertEqual(calls[0].full_url, "https://graph.facebook.com/v23.0/123456789/media")
        self.assertIn(b"%PDF-synthetic-report", calls[0].data)
        sent = json.loads(calls[1].data)
        self.assertEqual(calls[1].full_url, "https://graph.facebook.com/v23.0/123456789/messages")
        self.assertEqual(sent["document"]["id"], "MEDIA-SYNTHETIC")
        self.assertEqual(sent["to"], OWNER)

    def test_financial_answer_reports_manual_balance_only(self):
        with closing(connect(self.database)) as db:
            answer, report_month = financial_answer(db, "câți bani disponibili am?", int(time.time()))
        self.assertIsNone(report_month)
        self.assertIn("introduci manual", answer)


if __name__ == "__main__":
    unittest.main()
