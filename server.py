"""Serve Jason's portfolio with browser-side Web3Forms email delivery.

Local development needs only Python 3.10+. Deploy the WSGI `application` with
a production server such as Waitress behind HTTPS. The original SMTP endpoint
remains available for backwards compatibility but is not used by the form.
"""

from dataclasses import dataclass
from email.errors import HeaderParseError, InvalidHeaderDefect
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
import hashlib
from html import escape
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import time
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 32768
UNAVAILABLE = "The contact form is temporarily unavailable. Please email me directly."
DOMAIN = re.compile(r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?")


def valid_email(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 254 or not value.isascii():
        return False
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = Address(addr_spec=value)
        return bool(parsed.username and DOMAIN.fullmatch(parsed.domain))
    except (ValueError, HeaderParseError, InvalidHeaderDefect):
        return False


@dataclass(frozen=True)
class MailSettings:
    host: str = "smtp.gmail.com"
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password: str = ""
    sender: str = ""
    recipient: str = "jasone9661@gmail.com"
    public_origin: str = ""
    timeout: int = 15
    web3forms_access_key: str = ""

    def validate(self):
        if not valid_email(self.sender) or not valid_email(self.recipient):
            raise ValueError("Configure valid server-side sender and recipient addresses.")
        if not self.host or not 1 <= self.port <= 65535:
            raise ValueError("Invalid SMTP host or port.")
        local_test = self.security == "none" and self.host in ("127.0.0.1", "::1", "localhost")
        if self.security not in ("starttls", "ssl") and not local_test:
            raise ValueError("SMTP requires TLS. Plain SMTP is allowed only for loopback tests.")
        if not local_test and not (self.username and self.password):
            raise ValueError("SMTP authentication has not been configured.")
        if bool(self.username) != bool(self.password):
            raise ValueError("Configure both SMTP username and password.")


def load_settings():
    path = ROOT / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("settings.json must contain a JSON object.")

    def setting(name, default=""):
        return os.environ.get(name, data.get(name, default))

    username = str(setting("SMTP_USERNAME")).strip()
    return MailSettings(
        host=str(setting("SMTP_HOST", "smtp.gmail.com")).strip(),
        port=int(setting("SMTP_PORT", 587)),
        security=str(setting("SMTP_SECURITY", "starttls")).lower().strip(),
        username=username,
        password=str(setting("SMTP_PASSWORD")),
        sender=str(setting("CONTACT_FROM", username)).strip(),
        recipient=str(setting("CONTACT_TO", "jasone9661@gmail.com")).strip(),
        public_origin=str(setting("PUBLIC_ORIGIN")).rstrip("/"),
        web3forms_access_key=str(setting("WEB3FORMS_ACCESS_KEY")).strip(),
    )


def send_message(settings, name, email, text):
    settings.validate()
    message = EmailMessage()
    message["From"] = formataddr(("Jason Escalante Portfolio", settings.sender))
    message["To"] = settings.recipient
    message["Reply-To"] = email
    message["Subject"] = f"Portfolio contact: {name}"
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=settings.sender.split("@", 1)[1])
    message.set_content(f"Name: {name}\nEmail: {email}\n\n{text}\n", charset="utf-8")
    context = ssl.create_default_context()
    if settings.security == "ssl":
        client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout, context=context)
    else:
        client = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
    with client:
        if settings.security == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if settings.username:
            client.login(settings.username, settings.password)
        refused = client.send_message(message, from_addr=settings.sender, to_addrs=[settings.recipient])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


class RateLimiter:
    """Share a small per-address send-attempt limit across local worker processes."""

    def __init__(self, path, limit=5, window=600, clock=time.time):
        self.path = Path(path)
        self.limit = limit
        self.window = window
        self.clock = clock

    def allow(self, client):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256(client.encode("utf-8")).hexdigest()
        now = self.clock()
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS attempts (client TEXT, sent_at REAL)")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM attempts WHERE sent_at <= ?", (now - self.window,))
                count = connection.execute("SELECT COUNT(*) FROM attempts WHERE client = ?", (key,)).fetchone()[0]
                if count >= self.limit:
                    return False
                connection.execute("INSERT INTO attempts (client, sent_at) VALUES (?, ?)", (key, now))
                return True
        finally:
            connection.close()


def validate_fields(data):
    if not isinstance(data, dict):
        raise ValueError("Send a JSON object.")
    fields = {}
    errors = {}
    for key in ("name", "email", "message", "website"):
        value = data.get(key, "")
        if not isinstance(value, str):
            errors[key] = "Enter text in this field."
            value = ""
        fields[key] = value.strip()
    name, email, text = fields["name"], fields["email"], fields["message"]
    if not 1 <= len(name) <= 100 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        errors["name"] = "Enter a name of 1–100 characters on one line."
    if not valid_email(email):
        errors["email"] = "Enter a valid email address."
    if not 1 <= len(text) <= 5000 or any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        errors["message"] = "Enter a message of 1–5,000 characters."
    if fields["website"]:
        errors["website"] = "Leave the website field empty."
    return fields, errors


class ContactApplication:
    def __init__(self, settings=None, limiter=None, index_path=None):
        self.settings = settings or load_settings()
        self.limiter = limiter or RateLimiter(ROOT / "instance" / "contact-rate.sqlite")
        self.html = Path(index_path or ROOT / "index.html").read_bytes().replace(
            b'name="access_key" value=""',
            ('name="access_key" value="' + escape(self.settings.web3forms_access_key, quote=True) + '"').encode("utf-8"),
            1,
        )

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")

        def respond(status, value, content_type="application/json; charset=utf-8", extra=()):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
            headers = [
                ("Content-Type", content_type), ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "strict-origin-when-cross-origin"),
                ("Content-Security-Policy", "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://api.web3forms.com; base-uri 'none'; form-action 'self' https://api.web3forms.com; frame-ancestors 'none'"),
                *extra,
            ]
            start_response(f"{status} {HTTPStatus(status).phrase}", headers)
            return [b"" if method == "HEAD" else body]

        if path == "/" and method in ("GET", "HEAD"):
            return respond(200, self.html, "text/html; charset=utf-8")
        if path != "/api/contact":
            return respond(404, {"ok": False, "message": "Not found."})
        if method != "POST":
            return respond(405, {"ok": False, "message": "Use the contact form to send a message."}, extra=[("Allow", "POST")])
        origin = environ.get("HTTP_ORIGIN")
        expected = self.settings.public_origin or (
            environ.get("wsgi.url_scheme", "http") + "://" + environ.get("HTTP_HOST", "localhost")
        )
        if origin and origin.rstrip("/") != expected:
            return respond(403, {"ok": False, "message": "Please send your message from this portfolio."})
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/json":
            return respond(415, {"ok": False, "message": "Use the contact form to send a message."})
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
        except (ValueError, TypeError):
            return respond(411, {"ok": False, "message": "A request length is required."})
        if not 0 < length <= MAX_BODY_BYTES:
            return respond(413, {"ok": False, "message": "Your message is too large."})
        try:
            data = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
            fields, errors = validate_fields(data)
        except (ValueError, UnicodeError):
            return respond(400, {"ok": False, "message": "Please check your message and try again."})
        if errors:
            return respond(422, {"ok": False, "message": "Please check the highlighted fields.", "errors": errors})
        try:
            self.settings.validate()
        except ValueError:
            return respond(503, {"ok": False, "message": UNAVAILABLE})
        try:
            if not self.limiter.allow(environ.get("REMOTE_ADDR") or "unknown"):
                return respond(429, {"ok": False, "message": "Please wait a few minutes before sending another message."}, extra=[("Retry-After", str(self.limiter.window))])
            send_message(self.settings, fields["name"], fields["email"], fields["message"])
        except (OSError, smtplib.SMTPException, sqlite3.Error, ValueError) as exc:
            LOGGER.warning("Contact submission failed (%s).", type(exc).__name__)
            return respond(503, {"ok": False, "message": UNAVAILABLE})
        return respond(200, {"ok": True, "message": "Thanks! Your message has been sent."})


application = ContactApplication()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Open http://127.0.0.1:{port} — local development server.")
    with make_server("127.0.0.1", port, application) as server:
        server.serve_forever()
