"""Private, loopback-only web UI for importing and reviewing local finances."""
from __future__ import annotations

import argparse
import hashlib
import html
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from http.cookies import SimpleCookie
from decimal import Decimal
from contextlib import closing
from datetime import date
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from booking_imports import import_booking_csv
from finance_store import (
    Observation,
    classify_observation,
    confirm_booking_reconciliation,
    confirm_reconciliation,
    connect,
    import_booking_records,
    import_creasoft_report,
    import_observations,
    monthly_summary,
    record_bank_balance,
    write_monthly_pdf_report,
)
from financial_imports import import_csv, import_xlsx, load_mapping
from main import citeste_raport
from whatsapp import (MAX_WEBHOOK_BYTES, MetaAPIError, MetaCloudAPI, WhatsAppConfig,
                      parse_text_messages, process_message, verify_signature)


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_MAPPING = {
    "source": "bank",
    "currency": "RON",
    "date_format": "%d.%m.%Y",
    "delimiter": ";",
    "columns": {"date": "DATA", "description": "DESCRIERE", "amount": "SUMA"},
}


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _valid_month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Luna trebuie să aibă formatul AAAA-LL.")
    date.fromisoformat(value + "-01")
    return value


def render_dashboard(db_path: Path, month: str, csrf_token: str, message: str = "", authenticated: bool = False) -> str:
    month = _valid_month(month)
    with closing(connect(db_path)) as db:
        summary = monthly_summary(db, month)
        imports = db.execute(
            "SELECT source,filename,imported_at,status FROM imports ORDER BY id DESC LIMIT 20"
        ).fetchall()
        unclassified = db.execute(
            "SELECT id,source,occurred_on,description,amount,currency FROM observations "
            "WHERE substr(occurred_on,1,7)=? AND kind IN ('unclassified','unassigned') "
            "AND duplicate_of IS NULL ORDER BY occurred_on DESC,id DESC LIMIT 100", (month,)
        ).fetchall()
        generic_matches = db.execute("""SELECT r.id,r.confidence,r.note,
            a.source AS left_source,a.occurred_on AS left_date,a.description AS left_description,
            a.amount AS left_amount,a.currency AS left_currency,
            b.source AS right_source,b.occurred_on AS right_date,b.description AS right_description,
            b.amount AS right_amount,b.currency AS right_currency
            FROM reconciliations r JOIN observations a ON a.id=r.left_id
            JOIN observations b ON b.id=r.right_id WHERE r.status='needs_review' ORDER BY r.id DESC LIMIT 100""").fetchall()
        booking_matches = db.execute("""SELECT r.id,r.note,e.payout_date,e.payout_value,e.payout_currency,
            o.occurred_on,o.description,o.amount,o.currency FROM booking_reconciliations r
            JOIN booking_entries e ON e.id=r.booking_entry_id
            JOIN observations o ON o.id=r.bank_observation_id
            WHERE r.status='needs_review' ORDER BY r.id DESC LIMIT 100""").fetchall()
        recent_creasoft = db.execute("""SELECT machine_label,activity,quantity,reported_sales,currency,
            cash_status,review_status FROM creasoft_reports ORDER BY id DESC LIMIT 30""").fetchall()

    activity_rows = []
    for activity, currencies in sorted(summary["activities"].items()):
        for currency, values in sorted(currencies.items()):
            activity_rows.append(
                f"<tr><td>{_e(activity)}</td><td>{_e(currency)}</td><td>{_e(values['income'])}</td>"
                f"<td>{_e(values['expense'])}</td><td>{_e(values['commission'])}</td>"
                f"<td>{_e(values['estimated_result'])}</td></tr>"
            )
    if not activity_rows:
        activity_rows.append('<tr><td colspan="6" class="empty">Nicio tranzacție clasificată pentru luna selectată.</td></tr>')

    def currency_list(title: str, rows: list[dict]) -> str:
        items = "".join(f"<li><b>{_e(row['amount'])} {_e(row['currency'])}</b></li>" for row in rows)
        return f"<div class='metric'><span>{_e(title)}</span><ul>{items or '<li>—</li>'}</ul></div>"

    def form_token() -> str:
        return f'<input type="hidden" name="csrf" value="{_e(csrf_token)}">'

    unclassified_rows = []
    for row in unclassified:
        opts = "".join(f'<option value="{kind}">{label}</option>' for kind, label in (
            ("income", "Venit"), ("expense", "Cheltuială"), ("commission", "Comision"),
            ("transfer", "Transfer"), ("unclassified", "Necesită clasificare")))
        unclassified_rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{} {}</td><td><form method='post' action='/classify'>"
            "{}<input type='hidden' name='month' value='{}'><input type='hidden' name='observation_id' value='{}'>"
            "<input name='activity' value='{}' required maxlength='80' aria-label='Activitate'>"
            "<select name='kind'>{}</select><button>Salvează clasificarea</button></form></td></tr>".format(
                _e(row["occurred_on"]), _e(row["source"]), _e(row["description"]), _e(row["amount"]),
                _e(row["currency"]), form_token(), _e(month), row["id"],
                _e("unassigned"), opts))

    generic_rows = []
    for row in generic_matches:
        generic_rows.append(
            "<tr><td>{} {}<br>{} — {}</td><td>{} {}<br>{} — {}</td><td>{}</td><td><form method='post' action='/confirm/general'>"
            "{}<input type='hidden' name='month' value='{}'><input type='hidden' name='reconciliation_id' value='{}'>"
            "<button>Confirmă asocierea</button></form></td></tr>".format(
                _e(row["left_date"]), _e(row["left_source"]), _e(row["left_description"]),
                _e(f"{row['left_amount']} {row['left_currency']}"), _e(row["right_date"]),
                _e(row["right_source"]), _e(row["right_description"]),
                _e(f"{row['right_amount']} {row['right_currency']}"), _e(row["note"]),
                form_token(), _e(month), row["id"]))

    booking_rows = []
    for row in booking_matches:
        booking_rows.append(
            "<tr><td>{} — {} {}</td><td>{} — {} {}</td><td>{}</td><td><form method='post' action='/confirm/booking'>"
            "{}<input type='hidden' name='month' value='{}'><input type='hidden' name='reconciliation_id' value='{}'>"
            "<button>Confirmă încasarea</button></form></td></tr>".format(
                _e(row["payout_date"]), _e(row["payout_value"]), _e(row["payout_currency"]),
                _e(row["occurred_on"]), _e(row["amount"]), _e(row["currency"]), _e(row["description"]),
                form_token(), _e(month), row["id"]))

    creasoft_rows = "".join(
        f"<tr><td>{_e(row['machine_label'])}</td><td>{_e(row['activity'])}</td><td>{_e(row['quantity'])}</td>"
        f"<td>{_e(row['reported_sales'])} {_e(row['currency'])}</td><td>{_e(row['cash_status'])}</td>"
        f"<td>{_e(row['review_status'])}</td></tr>" for row in recent_creasoft)
    import_rows = "".join(
        f"<tr><td>{_e(row['imported_at'])}</td><td>{_e(row['source'])}</td><td>{_e(row['filename'])}</td>"
        f"<td>{_e(row['status'])}</td></tr>" for row in imports)
    receivables = currency_list("Creanțe Booking încă neîncasate", summary["booking_receivables_not_cash"])
    bank_funds = currency_list("Încasări confirmate în bancă", summary["bank_received_totals"])
    payout_candidates = currency_list("Plăți Booking raportate, neconfirmate", summary["booking_reported_payouts_not_bank_confirmed"])
    available = currency_list("Bani disponibili · sold bancar introdus manual", summary["bank_available_balances"])
    monthly_totals: dict[str, dict[str, Decimal]] = {}
    for currencies in summary["activities"].values():
        for currency, values in currencies.items():
            bucket = monthly_totals.setdefault(currency, {"income": Decimal(0), "expense": Decimal(0), "result": Decimal(0)})
            bucket["income"] += Decimal(values["income"])
            bucket["expense"] += Decimal(values["expense"]) + Decimal(values["commission"])
            bucket["result"] += Decimal(values["estimated_result"])
    def total_card(title: str, key: str) -> str:
        amounts = "".join(f"<li><b>{values[key]:.2f} {_e(currency)}</b></li>" for currency, values in sorted(monthly_totals.items()))
        return f"<div class='metric'><span>{_e(title)}</span><ul>{amounts or '<li>—</li>'}</ul></div>"
    incomes = total_card("Venituri clasificate în lună", "income")
    expenses = total_card("Cheltuieli și comisioane", "expense")
    profit = total_card("Profit estimat", "result")
    message_html = f"<p class='notice'>{_e(message)}</p>" if message else ""
    unclassified_html = "".join(unclassified_rows) or '<tr><td colspan="5" class="empty">Nicio tranzacție neclasificată.</td></tr>'
    generic_html = "".join(generic_rows) or '<tr><td colspan="4" class="empty">Nicio asociere în așteptare.</td></tr>'
    booking_html = "".join(booking_rows) or '<tr><td colspan="4" class="empty">Nicio asociere Booking în așteptare.</td></tr>'
    return f"""<!doctype html><html lang="ro"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Asistent Financiar Personal</title><style>
:root{{font-family:system-ui,sans-serif;color:#18222f;background:#f2f5f8}}body{{max-width:1180px;margin:0 auto;padding:24px}}h1{{margin-bottom:4px}}h2{{margin-top:30px}}.sub{{color:#586779}}.card,.panel{{background:white;border:1px solid #dce3ea;border-radius:12px;padding:18px;margin:16px 0}}.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}.metric{{background:#f8fafc;border-radius:8px;padding:12px}}.metric span{{color:#586779}}.metric ul{{padding-left:18px;margin-bottom:0}}form.inline{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}input,select,textarea,button{{font:inherit;padding:8px;border:1px solid #bbc6d2;border-radius:6px}}input[type=file]{{max-width:100%}}button{{background:#154f7c;color:white;border:0;cursor:pointer}}button:hover{{background:#0f3d61}}textarea{{width:min(100%,700px);height:115px;font-family:monospace}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:9px;border-bottom:1px solid #e4e9ee;text-align:left;vertical-align:top}}th{{background:#f4f7fa}}.scroll{{overflow:auto}}.empty{{color:#697788}}.notice{{background:#e7f4ff;padding:12px;border-radius:8px}}.mapping{{display:block;margin:10px 0}}.safety{{font-size:.92rem;color:#526171}}@media(max-width:640px){{body{{padding:12px}}}}
</style><body><h1>Asistent Financiar Personal</h1><div class="sub">Aplicație locală/cloud privată · extrasele bancare se importă manual</div>{message_html}
{"<form method='post' action='/logout'>" + form_token() + "<button>Deconectare</button></form>" if authenticated else ""}
<section class="card"><form class="inline" method="get"><label for="month">Perioadă raportată:</label><input id="month" type="month" name="month" value="{_e(month)}"><button>Vezi luna</button></form>
<div class="metrics">{incomes}{expenses}{profit}{available}{bank_funds}{receivables}{payout_candidates}</div></section>
<section class="panel"><h2>Import Creasoft</h2><form method="post" action="/import/creasoft" enctype="multipart/form-data">{form_token()}<input type="hidden" name="month" value="{_e(month)}"><input type="file" name="file" accept=".csv,text/csv" required><button>Importă raportul CSV</button></form><p class="safety">Totalurile combinate cash + non-cash nu sunt interpretate ca numerar fizic. Perioada nu este dedusă dacă lipsește din raport.</p></section>
<section class="panel"><h2>Import raport financiar Booking.com</h2><form method="post" action="/import/booking" enctype="multipart/form-data">{form_token()}<input type="hidden" name="month" value="{_e(month)}"><input type="file" name="file" accept=".csv,text/csv" required><button>Importă raportul CSV</button></form><p class="safety">Plățile rămân neconfirmate până la asocierea cu extrasul bancar și confirmarea ta.</p></section>
<section class="panel"><h2>Import extras bancar</h2><form method="post" action="/import/bank" enctype="multipart/form-data">{form_token()}<input type="hidden" name="month" value="{_e(month)}"><label class="mapping">Fișier CSV sau XLSX: <input type="file" name="file" accept=".csv,.xlsx" required></label><label class="mapping">Maparea JSON cu antetele exacte ale extrasului:</label><textarea name="mapping" required>{_e(json.dumps(DEFAULT_MAPPING,ensure_ascii=False,indent=2))}</textarea><button>Importă și salvează în registru</button></form><p class="safety">Câmpurile nemapate rămân neclasificate. Completează DATA, DESCRIERE și SUMA cu antetele reale ale fișierului; nu sunt presupuse formatele băncii.</p></section>
<section class="panel"><h2>Sold bancar disponibil</h2><form class="inline" method="post" action="/bank-balance">{form_token()}<input type="hidden" name="month" value="{_e(month)}"><label>Sold din extras: <input name="amount" inputmode="decimal" required aria-label="Sold bancar"></label><label>Monedă: <input name="currency" value="RON" minlength="3" maxlength="3" required></label><label>Valabil la: <input type="date" name="as_of" value="{date.today().isoformat()}" required></label><button>Salvează soldul</button></form><p class="safety">Soldul se introduce manual din extras. Aplicația nu se conectează la bancă; încasările lunii și creanțele nu sunt sold disponibil.</p></section>
<section class="panel"><h2>Raport lunar</h2><a href="/report.pdf?month={_e(month)}">Descarcă raportul PDF pentru {_e(month)}</a></section>
<section class="panel"><h2>Situația financiară · {_e(month)}</h2><div class="scroll"><table><thead><tr><th>Activitate</th><th>Monedă</th><th>Venit</th><th>Cheltuieli</th><th>Comisioane</th><th>Rezultat estimat</th></tr></thead><tbody>{''.join(activity_rows)}</tbody></table></div><p class="safety">Rezultatele se calculează doar din tranzacțiile clasificate și sunt estimative. Nu se fac conversii între monede.</p></section>
<section class="panel"><h2>Potriviri propuse · necesită confirmare</h2><h3>Booking cu banca</h3><div class="scroll"><table><thead><tr><th>Plată Booking</th><th>Extras bancar</th><th>Notă</th><th></th></tr></thead><tbody>{booking_html}</tbody></table></div><h3>Alte potriviri între surse</h3><div class="scroll"><table><thead><tr><th>Înregistrare 1</th><th>Înregistrare 2</th><th>Notă</th><th></th></tr></thead><tbody>{generic_html}</tbody></table></div></section>
<section class="panel"><h2>Tranzacții de clasificat · {_e(month)}</h2><div class="scroll"><table><thead><tr><th>Data</th><th>Sursă</th><th>Descriere</th><th>Sumă</th><th>Clasificare</th></tr></thead><tbody>{unclassified_html}</tbody></table></div></section>
<section class="panel"><h2>Rapoarte Creasoft importate</h2><div class="scroll"><table><thead><tr><th>Etichetă</th><th>Activitate</th><th>Produse</th><th>Vânzări raportate</th><th>Numerar</th><th>Revizuire</th></tr></thead><tbody>{creasoft_rows or '<tr><td colspan="6" class="empty">Niciun raport importat.</td></tr>'}</tbody></table></div></section>
<section class="panel"><h2>Importuri recente</h2><div class="scroll"><table><thead><tr><th>Importat la</th><th>Sursă</th><th>Fișier</th><th>Stare</th></tr></thead><tbody>{import_rows or '<tr><td colspan="4" class="empty">Niciun fișier importat.</td></tr>'}</tbody></table></div></section>
<footer class="safety">Extrasele bancare sunt importate manual. Nu există sincronizare bancară automată; fișierele încărcate sunt procesate local de aplicație.</footer></body></html>"""


class FinanceRequestHandler(BaseHTTPRequestHandler):
    server_version = "PersonalFinanceLocal/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Do not log upload paths, query text, or financial data.
        return

    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, month: str, message: str) -> None:
        self.send_response(303)
        self.send_header("Location", f"/?month={month}&message={quote(message)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _session_token(self) -> str:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        return cookie.get("finance_session").value if cookie.get("finance_session") else ""

    def _is_authenticated(self) -> bool:
        if not self.server.auth_password_hash:
            return True
        token = self._session_token()
        expiry = self.server.sessions.get(token)
        if expiry is None:
            return False
        if expiry <= time.time():
            self.server.sessions.pop(token, None)
            return False
        return True

    def _login_page(self, status: int = 200, message: str = "") -> None:
        error = f"<p role='alert'>{_e(message)}</p>" if message else ""
        body = ("<!doctype html><html lang='ro'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
                "<title>Autentificare · Asistent financiar</title><h1>Autentificare</h1>" + error +
                '<form method="post" action="/login"><input type="hidden" name="csrf" value="' +
                _e(self.server.csrf_token) + '"><label>Parolă <input type="password" name="password" required '
                "autocomplete='current-password'></label><button>Intră</button></form></html>")
        self._send(status, body)

    def _origin_is_valid(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        expected = self.server.public_origin or f"http://{self.headers.get('Host', '')}"
        return hmac.compare_digest(origin.rstrip("/"), expected.rstrip("/"))

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/webhook/whatsapp":
            config = self.server.whatsapp_config
            params = parse_qs(parsed.query, keep_blank_values=True)
            mode = params.get("hub.mode", [""])[0]
            token = params.get("hub.verify_token", [""])[0]
            challenge = params.get("hub.challenge", [""])[0]
            if (config.verify_token and mode == "subscribe" and
                    secrets.compare_digest(token, config.verify_token) and
                    challenge and len(challenge) <= 256 and challenge.isascii()):
                self._send(200, challenge, "text/plain; charset=utf-8")
            else:
                self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        if parsed.path == "/healthz":
            try:
                with closing(connect(self.server.db_path)) as db:
                    db.execute("SELECT 1")
                self._send(200, "ok", "text/plain; charset=utf-8")
            except OSError:
                self._send(503, "unavailable", "text/plain; charset=utf-8")
            return
        if parsed.path == "/login":
            if self._is_authenticated():
                self._redirect(date.today().strftime("%Y-%m"), "Sesiunea este activă.")
            else:
                self._login_page()
            return
        if self.server.auth_password_hash and not self._is_authenticated():
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path == "/report.pdf":
            month = parse_qs(parsed.query).get("month", [date.today().strftime("%Y-%m")])[0]
            try:
                month = _valid_month(month)
                with tempfile.TemporaryDirectory(prefix="asistent-financiar-pdf-") as folder:
                    path = Path(folder) / f"raport-financiar-{month}.pdf"
                    with closing(connect(self.server.db_path)) as db:
                        write_monthly_pdf_report(db, month, path)
                    data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'attachment; filename="raport-financiar-{month}.pdf"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (ValueError, OSError) as exc:
                self._send(400, f"<p>{_e(exc)}</p>")
            return
        if parsed.path != "/":
            self._send(404, "Nu există această pagină.")
            return
        params = parse_qs(parsed.query)
        month = params.get("month", [date.today().strftime("%Y-%m")])[0]
        message = params.get("message", [""])[0]
        try:
            page = render_dashboard(self.server.db_path, month, self.server.csrf_token, message,
                                    authenticated=bool(self.server.auth_password_hash))
        except (ValueError, OSError) as exc:
            self._send(400, f"<p>{_e(exc)}</p>")
            return
        self._send(200, page)

    def _read_form(self) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Dimensiunea cererii nu este validă.") from exc
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("Fișierul lipsește sau depășește limita de 10 MB.")
        content_type = self.headers.get("Content-Type", "")
        raw = self.rfile.read(length)
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type == "application/x-www-form-urlencoded":
            try:
                encoded_fields = parse_qs(
                    raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                    max_num_fields=100,
                )
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Formularul nu este valid.") from exc
            if any(len(values) != 1 for values in encoded_fields.values()):
                raise ValueError("Formularul conține câmpuri duplicate.")
            return {name: values[0] for name, values in encoded_fields.items()}, {}
        if media_type != "multipart/form-data":
            raise ValueError("Tipul formularului nu este acceptat.")
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + raw
        )
        fields: dict[str, str] = {}
        files: dict[str, tuple[str, bytes]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename is not None:
                files[name] = (Path(filename.replace("\\", "/")).name, payload)
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="strict")
        return fields, files

    def _check_origin_and_csrf(self, fields: dict[str, str]) -> None:
        origin = self.headers.get("Origin")
        if not self._origin_is_valid():
            raise ValueError("Cererea externă a fost respinsă.")
        if not secrets.compare_digest(fields.get("csrf", ""), self.server.csrf_token):
            raise ValueError("Token de formular invalid. Reîncarcă pagina și încearcă din nou.")

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/webhook/whatsapp":
            self._handle_whatsapp_webhook()
            return
        if self.server.auth_password_hash and parsed.path not in {"/login", "/logout"} and not self._is_authenticated():
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        try:
            fields, files = self._read_form()
            if not self._origin_is_valid():
                raise ValueError("Cererea externă a fost respinsă.")
            if not secrets.compare_digest(fields.get("csrf", ""), self.server.csrf_token):
                raise ValueError("Token de formular invalid. Reîncarcă pagina și încearcă din nou.")
            if parsed.path == "/login":
                if not self.server.auth_password_hash:
                    self._redirect(date.today().strftime("%Y-%m"), "Autentificarea nu este configurată.")
                    return
                candidate = _derive_password_hash(fields.get("password", ""), bytes.fromhex(self.server.password_salt))
                if not hmac.compare_digest(candidate, self.server.auth_password_hash):
                    self._login_page(401, "Parola nu este corectă.")
                    return
                token = secrets.token_urlsafe(32)
                self.server.sessions[token] = time.time() + 12 * 60 * 60
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"finance_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200" + ("; Secure" if self.server.secure_cookies else ""))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if self.server.auth_password_hash and not self._is_authenticated():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if parsed.path == "/logout":
                self.server.sessions.pop(self._session_token(), None)
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Set-Cookie", "finance_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + ("; Secure" if self.server.secure_cookies else ""))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            month = _valid_month(fields.get("month", date.today().strftime("%Y-%m")))
            if parsed.path in {"/import/creasoft", "/import/booking", "/import/bank"}:
                filename, content = files.get("file", ("", b""))
                if not filename or not content:
                    raise ValueError("Selectează un fișier care conține date.")
                if parsed.path == "/import/creasoft":
                    if Path(filename).suffix.lower() != ".csv":
                        raise ValueError("Importul Creasoft acceptă CSV.")
                    with tempfile.TemporaryDirectory(prefix="asistent-financiar-") as folder:
                        path = Path(folder) / filename
                        path.write_bytes(content)
                        aggregates = citeste_raport(path)
                    with closing(connect(self.server.db_path)) as db:
                        _, count = import_creasoft_report(db, filename, content, aggregates)
                    message = f"Creasoft: importate {count} agregate; fișierul identic nu este reimportat."
                elif parsed.path == "/import/booking":
                    if Path(filename).suffix.lower() != ".csv":
                        raise ValueError("Importul Booking acceptă CSV.")
                    with tempfile.TemporaryDirectory(prefix="asistent-financiar-") as folder:
                        path = Path(folder) / filename
                        path.write_bytes(content)
                        records = import_booking_csv(path)
                    with closing(connect(self.server.db_path)) as db:
                        _, count = import_booking_records(db, filename, content, records)
                    message = f"Booking: importate {count} rânduri; plățile propuse nu sunt confirmate automat."
                else:
                    suffix = Path(filename).suffix.lower()
                    if suffix not in {".csv", ".xlsx"}:
                        raise ValueError("Extrasul bancar trebuie să fie CSV sau XLSX.")
                    try:
                        mapping_data = json.loads(fields.get("mapping", ""))
                    except json.JSONDecodeError as exc:
                        raise ValueError("Maparea trebuie să fie JSON valid.") from exc
                    with tempfile.TemporaryDirectory(prefix="asistent-financiar-") as folder:
                        path = Path(folder) / filename
                        path.write_bytes(content)
                        mapping_path = Path(folder) / "mapping.json"
                        mapping_path.write_text(json.dumps(mapping_data, ensure_ascii=False), encoding="utf-8")
                        mapping = load_mapping(mapping_path)
                        records = import_xlsx(path, mapping) if suffix == ".xlsx" else import_csv(path, mapping)
                    has_settlement_mapping = "settlement" in mapping["columns"]
                    observations = [Observation(
                        source="bank", occurred_on=row.transaction_date, description=row.description,
                        amount=row.amount, currency=row.currency, activity=row.activity, kind=row.kind,
                        settlement=row.settlement if has_settlement_mapping else "received",
                        external_id=row.external_id, counterparty=row.counterparty,
                        source_row=row.source_row,
                    ) for row in records]
                    with closing(connect(self.server.db_path)) as db:
                        _, count = import_observations(db, filename, content, observations)
                    message = f"Extras bancar: importate {count} tranzacții; duplicatele de fișier nu se reimportă."
            elif parsed.path == "/bank-balance":
                with closing(connect(self.server.db_path)) as db:
                    record_bank_balance(db, fields.get("amount", ""), fields.get("currency", ""), fields.get("as_of", ""))
                message = "Soldul bancar introdus manual a fost salvat."
            elif parsed.path == "/confirm/general":
                with closing(connect(self.server.db_path)) as db:
                    confirm_reconciliation(db, int(fields.get("reconciliation_id", "")))
                message = "Potrivirea a fost confirmată."
            elif parsed.path == "/confirm/booking":
                with closing(connect(self.server.db_path)) as db:
                    confirm_booking_reconciliation(db, int(fields.get("reconciliation_id", "")))
                message = "Încasarea Booking a fost confirmată după potrivirea cu extrasul bancar."
            elif parsed.path == "/classify":
                activity = fields.get("activity", "").strip()
                kind = fields.get("kind", "")
                if not activity or kind not in {"income", "expense", "commission", "transfer", "unclassified"}:
                    raise ValueError("Alege o activitate și o clasificare validă.")
                with closing(connect(self.server.db_path)) as db:
                    classify_observation(db, int(fields.get("observation_id", "")), activity=activity, kind=kind)
                message = "Clasificarea a fost salvată."
            else:
                self._send(404, "Nu există această acțiune.")
                return
            self._redirect(month, message)
        except (ValueError, OSError, TypeError, OverflowError) as exc:
            month = fields.get("month", date.today().strftime("%Y-%m")) if "fields" in locals() else date.today().strftime("%Y-%m")
            try:
                month = _valid_month(month)
            except ValueError:
                month = date.today().strftime("%Y-%m")
            self._redirect(month, f"Nu s-a putut procesa cererea: {exc}")

    def _handle_whatsapp_webhook(self) -> None:
        config = self.server.whatsapp_config
        if not config.webhook_ready:
            self._send(503, "webhook not configured", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, "invalid request", "text/plain; charset=utf-8")
            return
        if length <= 0 or length > MAX_WEBHOOK_BYTES:
            self._send(413, "payload too large", "text/plain; charset=utf-8")
            return
        body = self.rfile.read(length)
        if len(body) != length or not verify_signature(
                config.app_secret, body, self.headers.get("X-Hub-Signature-256", "")):
            self._send(401, "invalid signature", "text/plain; charset=utf-8")
            return
        try:
            payload = json.loads(body)
            messages = parse_text_messages(payload)
            api = MetaCloudAPI(config) if config.messaging_ready else None

            def send_text(recipient: str, text: str) -> None:
                if api is None:
                    raise MetaAPIError("Mesageria WhatsApp neconfigurată.")
                api.send_text(recipient, text)

            def send_pdf(recipient: str, path: Path, caption: str) -> None:
                if api is None:
                    raise MetaAPIError("Mesageria WhatsApp neconfigurată.")
                api.send_pdf(recipient, path, caption)

            from finance_store import write_monthly_pdf_report
            with closing(connect(self.server.db_path)) as db:
                for message in messages:
                    process_message(db, message, config, int(time.time()), send_text, send_pdf,
                                    write_monthly_pdf_report)
        except MetaAPIError:
            self._send(502, "message delivery failed", "text/plain; charset=utf-8")
            return
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            self._send(400, "invalid webhook payload", "text/plain; charset=utf-8")
            return
        except Exception:
            # Do not leak financial data, webhook payloads, phone numbers, or API responses.
            self._send(500, "webhook processing failed", "text/plain; charset=utf-8")
            return
        self._send(200, "EVENT_RECEIVED", "text/plain; charset=utf-8")


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Create a PBKDF2-SHA256 hash for APP_PASSWORD_HASH configuration."""
    if len(password) < 14:
        raise ValueError("Parola cloud trebuie să aibă cel puțin 14 caractere.")
    salt = salt or secrets.token_bytes(16)
    return _derive_password_hash(password, salt), salt.hex()


def _derive_password_hash(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return f"pbkdf2_sha256$600000${digest.hex()}"


class FinanceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, db_path: Path, port: int = 8765, host: str = "127.0.0.1",
                 password_hash: str | None = None, password_salt: str | None = None,
                 public_origin: str | None = None, secure_cookies: bool | None = None):
        password_hash = password_hash if password_hash is not None else os.environ.get("APP_PASSWORD_HASH", "")
        password_salt = password_salt if password_salt is not None else os.environ.get("APP_PASSWORD_SALT", "")
        public_origin = public_origin if public_origin is not None else os.environ.get("APP_PUBLIC_URL", "")
        secure_cookies = secure_cookies if secure_cookies is not None else public_origin.startswith("https://")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            if not password_hash or not re.fullmatch(r"pbkdf2_sha256\$600000\$[0-9a-f]{64}", password_hash):
                raise ValueError("Expunerea publică necesită APP_PASSWORD_HASH PBKDF2 valid.")
            if not re.fullmatch(r"[0-9a-f]{32}", password_salt):
                raise ValueError("Expunerea publică necesită APP_PASSWORD_SALT valid.")
            if not public_origin.startswith("https://") or not secure_cookies:
                raise ValueError("Expunerea publică necesită APP_PUBLIC_URL HTTPS și cookie-uri Secure.")
        super().__init__((host, port), FinanceRequestHandler)
        self.db_path = db_path
        self.csrf_token = secrets.token_urlsafe(32)
        self.auth_password_hash = password_hash
        self.password_salt = password_salt
        self.public_origin = public_origin
        self.secure_cookies = secure_cookies
        self.sessions: dict[str, float] = {}
        self.whatsapp_config = WhatsAppConfig.from_env()


def main() -> int:
    parser = argparse.ArgumentParser(description="Interfață financiară locală, fără servicii externe.")
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", "data/finante.sqlite3")))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--host", default=os.environ.get("APP_HOST", "127.0.0.1"))
    parser.add_argument("--hash-password", action="store_true", help="afișează hash-ul unei parole cloud introduse interactiv")
    args = parser.parse_args()
    if args.hash_password:
        import getpass
        password = getpass.getpass("Parolă cloud (minim 14 caractere): ")
        try:
            password_hash, password_salt = hash_password(password)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"APP_PASSWORD_HASH={password_hash}")
        print(f"APP_PASSWORD_SALT={password_salt}")
        return 0
    if not 1024 <= args.port <= 65535:
        parser.error("--port trebuie să fie între 1024 și 65535.")
    with closing(connect(args.database)) as db:
        db.execute("SELECT 1")
    try:
        server = FinanceHTTPServer(args.database, args.port, args.host)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Asistentul financiar este disponibil la http://{args.host}:{args.port}")
    print("Pentru oprire, apasă Ctrl+C în această fereastră.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
