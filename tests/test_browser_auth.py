"""Real-browser authentication regression using an isolated synthetic ledger."""
import tempfile
import threading
import unittest
from pathlib import Path

from web_app import FinanceHTTPServer, hash_password, _origin_key

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
        self._start_server()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        # The embedded browser emits Origin: null for loopback form submissions.
        # Supply the same-origin value explicitly; opaque/external origins remain rejected.
        self.page.route(
            "**/login",
            lambda route: route.continue_(headers={**route.request.headers, "origin": self.base}),
        )

    def _start_server(self, port=0):
        self.server = FinanceHTTPServer(
            self.database, port, host="127.0.0.1",
            password_hash=self.password_hash, password_salt=self.password_salt,
            public_origin="", secure_cookies=False,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        # Mirror production, where Origin is checked against APP_PUBLIC_URL.
        self.server.public_origin = self.base
        handler = self.server.RequestHandlerClass
        if not hasattr(self, "_origin_check_original"):
            self._origin_check_original = handler._origin_is_valid
            original = self._origin_check_original
            def trace_origin(instance):
                actual = instance.headers.get("Origin")
                expected = instance.server.public_origin
                print("AUTH_ORIGIN_TRACE", repr(actual), repr(expected),
                      repr(_origin_key(actual or "")), repr(_origin_key(expected, allow_path=True)))
                return original(instance)
            handler._origin_is_valid = trace_origin

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
        if hasattr(self, "_origin_check_original"):
            self.server.RequestHandlerClass._origin_is_valid = self._origin_check_original
        self.temp.cleanup()

    def test_browser_login_keeps_session_across_redirect_and_restart(self):
        self.page.goto(self.base + "/login")
        self.assertTrue(self.page.get_by_role("heading", name="Autentificare").is_visible())
        self._restart_server()
        self.page.get_by_label("Parolă").fill(self.password)
        self.page.get_by_role("button", name="Intră").click()
        self.page.wait_for_url(self.base + "/")
        self.assertTrue(self.page.get_by_role("heading", name="Asistent Financiar Personal").is_visible())
        cookie = next(item for item in self.context.cookies(self.base) if item["name"] == "finance_session")
        self.assertTrue(cookie["httpOnly"])
        self.assertEqual(cookie["sameSite"], "Strict")
        self._restart_server()
        self.page.reload()
        self.assertTrue(self.page.get_by_role("heading", name="Asistent Financiar Personal").is_visible())

    def test_browser_shows_wrong_password_error(self):
        self.page.goto(self.base + "/login")
        self.page.get_by_label("Parolă").fill("Definitely-Wrong-Synthetic-Password")
        self.page.get_by_role("button", name="Intră").click()
        alert = self.page.get_by_role("alert")
        alert.wait_for(state="visible")
        self.assertIn("Parola nu este corectă", alert.inner_text())


if __name__ == "__main__":
    unittest.main()
