"""Real-browser authentication regression using an isolated synthetic ledger."""
import ssl
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from web_app import FinanceHTTPServer, hash_password

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Playwright test dependency is not installed")
class BrowserAuthenticationTests(unittest.TestCase):
    password = "Synthetic-Browser-Password-Only"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "synthetic-ledger.sqlite3"
        self.password_hash, self.password_salt = hash_password(self.password)
        self.server = None
        self.thread = None
        self._create_test_certificate()
        self._start_server()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(ignore_https_errors=True)
        self.page = self.context.new_page()

    def _create_test_certificate(self):
        now = datetime.now(timezone.utc)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_path = Path(self.temp.name) / "test-key.pem"
        cert_path = Path(self.temp.name) / "test-cert.pem"
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        self.test_key_path = key_path
        self.test_cert_path = cert_path

    def _start_server(self, port=0):
        self.server = FinanceHTTPServer(
            self.database, port, host="127.0.0.1",
            password_hash=self.password_hash, password_salt=self.password_salt,
            public_origin="", secure_cookies=False,
        )
        self.base = f"https://127.0.0.1:{self.server.server_port}"
        self.server.public_origin = self.base
        self.server.secure_cookies = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.test_cert_path, self.test_key_path)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _restart_server(self):
        old, thread, port = self.server, self.thread, self.server.server_port
        old.shutdown()
        old.server_close()
        thread.join(timeout=2)
        self._start_server(port)

    def tearDown(self):
        if getattr(self, "browser", None):
            self.browser.close()
            self.playwright.stop()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_browser_login_keeps_session_across_redirect_and_restart(self):
        self.page.goto(self.base + "/login")
        print("AUTH_DOCUMENT", self.page.url, self.page.evaluate("window.location.origin"))
        self.assertTrue(self.page.get_by_role("heading", name="Autentificare").is_visible())
        self._restart_server()
        self.page.get_by_label("Parolă").fill(self.password)
        self.page.get_by_role("button", name="Intră").click()
        self.page.wait_for_url(self.base + "/")
        self.assertTrue(self.page.get_by_role("heading", name="Asistent Financiar Personal").is_visible())
        cookie = next(item for item in self.context.cookies(self.base) if item["name"] == "finance_session")
        self.assertTrue(cookie["httpOnly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["sameSite"], "Strict")
        self._restart_server()
        self.page.reload()
        self.assertTrue(self.page.get_by_role("heading", name="Asistent Financiar Personal").is_visible())

    def test_browser_shows_wrong_password_error(self):
        self.page.goto(self.base + "/login")
        print("AUTH_DOCUMENT", self.page.url, self.page.evaluate("window.location.origin"))
        self.page.get_by_label("Parolă").fill("Definitely-Wrong-Synthetic-Password")
        self.page.get_by_role("button", name="Intră").click()
        alert = self.page.get_by_role("alert")
        alert.wait_for(state="visible")
        self.assertIn("Parola nu este corectă", alert.inner_text())


if __name__ == "__main__":
    unittest.main()
