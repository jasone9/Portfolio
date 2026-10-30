import io
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
import queue
import smtplib
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server, WSGIRequestHandler
from wsgiref.util import setup_testing_defaults

import server

MESSAGE = {"name": "Example Visitor", "email": "visitor@example.com", "message": "Hello Jason!\nI would like to discuss a project.", "website": ""}


def request(app, data=None, method="POST", path="/api/contact", raw=None, **extra):
    body = raw if raw is not None else json.dumps(MESSAGE if data is None else data).encode()
    environ = {}
    setup_testing_defaults(environ)
    environ.update({"REQUEST_METHOD": method, "PATH_INFO": path, "HTTP_HOST": "localhost",
                    "REMOTE_ADDR": "192.0.2.10", "CONTENT_TYPE": "application/json",
                    "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), **extra})
    result = {}

    def start_response(status, headers):
        result["status"] = int(status.split()[0])
        result["headers"] = dict(headers)

    result["body"] = b"".join(app(environ, start_response))
    if result["body"] and result["headers"]["Content-Type"].startswith("application/json"):
        result["json"] = json.loads(result["body"])
    return result


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = server.MailSettings(username="owner@example.com", password="server-only-secret", sender="owner@example.com")
        self.limiter = server.RateLimiter(Path(self.directory.name) / "limits.sqlite")
        self.app = server.ContactApplication(self.settings, self.limiter)
        smtp = patch.object(server.smtplib, "SMTP")
        self.smtp_factory = smtp.start()
        self.addCleanup(smtp.stop)
        self.smtp = self.smtp_factory.return_value
        self.smtp.send_message.return_value = {}

    def test_message_uses_fixed_recipient_and_visitor_reply_to(self):
        result = request(self.app, {**MESSAGE, "to": "unintended@example.com"})
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["json"]["ok"])
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(str(message["To"]), "jasone9661@gmail.com")
        self.assertEqual(str(message["Reply-To"]), "visitor@example.com")
        self.assertIn("owner@example.com", str(message["From"]))
        self.assertIn(MESSAGE["message"], message.get_content())
        self.assertTrue(message["Date"])
        self.assertTrue(message["Message-ID"])
        self.assertEqual(self.smtp.send_message.call_args.kwargs["to_addrs"], ["jasone9661@gmail.com"])
        self.smtp.starttls.assert_called_once()
        self.smtp.login.assert_called_once_with("owner@example.com", "server-only-secret")

    def test_missing_credentials_fail_without_sending_or_fake_success(self):
        app = server.ContactApplication(server.MailSettings(), self.limiter)
        result = request(app)
        self.assertEqual(result["status"], 503)
        self.assertFalse(result["json"]["ok"])
        self.smtp_factory.assert_not_called()

    def test_required_fields_and_header_injection_are_rejected(self):
        for changes in ({"name": ""}, {"email": ""}, {"email": "bad"}, {"message": " "},
                        {"name": "Visitor\r\nBcc: unwanted@example.com"},
                        {"email": "visitor@example.com\nBcc: unwanted@example.com"},
                        {"email": "a..b@example.com"}, {"email": "a@example..com"},
                        {"email": ["visitor@example.com"]}, {"message": "a" * 5001}):
            with self.subTest(changes=changes):
                result = request(self.app, {**MESSAGE, **changes})
                self.assertEqual(result["status"], 422)
                self.assertFalse(result["json"]["ok"])
        self.smtp_factory.assert_not_called()

    def test_honeypot_rejects_bot_submission(self):
        self.assertEqual(request(self.app, {**MESSAGE, "website": "spam.example"})["status"], 422)
        self.smtp_factory.assert_not_called()

    def test_invalid_json_shape_and_encoding_are_rejected(self):
        for raw in (b"not json", b"[]", b"null", b"\xff"):
            with self.subTest(raw=raw):
                self.assertEqual(request(self.app, raw=raw)["status"], 400)
        self.smtp_factory.assert_not_called()

    def test_cross_origin_and_native_form_posts_are_rejected(self):
        self.assertEqual(request(self.app, HTTP_ORIGIN="https://other.example")["status"], 403)
        self.assertEqual(request(self.app, CONTENT_TYPE="application/x-www-form-urlencoded")["status"], 415)
        self.smtp_factory.assert_not_called()

    def test_configured_public_origin_works_behind_a_proxy(self):
        settings = server.MailSettings(username="owner@example.com", password="secret", sender="owner@example.com", public_origin="https://portfolio.example")
        app = server.ContactApplication(settings, self.limiter)
        self.assertEqual(request(app, HTTP_ORIGIN="https://portfolio.example")["status"], 200)

    def test_large_or_missing_length_requests_do_not_send(self):
        self.assertEqual(request(self.app, CONTENT_LENGTH=str(server.MAX_BODY_BYTES + 1))["status"], 413)
        self.assertEqual(request(self.app, CONTENT_LENGTH="")["status"], 411)
        self.smtp_factory.assert_not_called()

    def test_rate_limit_counts_attempts_across_application_instances(self):
        for _ in range(5):
            self.assertEqual(request(self.app)["status"], 200)
        second_app = server.ContactApplication(self.settings, server.RateLimiter(self.limiter.path))
        result = request(second_app)
        self.assertEqual(result["status"], 429)
        self.assertIn("Retry-After", result["headers"])
        self.assertEqual(self.smtp.send_message.call_count, 5)
        self.assertEqual(request(second_app, REMOTE_ADDR="192.0.2.11")["status"], 200)

    def test_rate_window_expires(self):
        now = [1000]
        limiter = server.RateLimiter(self.limiter.path, limit=1, window=60, clock=lambda: now[0])
        self.assertTrue(limiter.allow("192.0.2.10"))
        self.assertFalse(limiter.allow("192.0.2.10"))
        now[0] += 61
        self.assertTrue(limiter.allow("192.0.2.10"))

    def test_mail_server_refusal_or_failure_never_reports_success(self):
        for error in (smtplib.SMTPAuthenticationError(535, b"private provider detail"),
                      smtplib.SMTPRecipientsRefused({"jasone9661@gmail.com": (550, b"refused")}),
                      TimeoutError("private transport detail")):
            with self.subTest(error=error):
                self.smtp.send_message.side_effect = error
                result = request(self.app)
                self.assertEqual(result["status"], 503)
                self.assertFalse(result["json"]["ok"])
                self.assertNotIn("private", result["body"].decode())
                self.assertNotIn("server-only-secret", result["body"].decode())

    def test_tls_failure_does_not_send_credentials(self):
        self.smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("No TLS")
        self.assertEqual(request(self.app)["status"], 503)
        self.smtp.login.assert_not_called()
        self.smtp.send_message.assert_not_called()

    def test_plain_smtp_is_allowed_only_on_loopback(self):
        settings = server.MailSettings(host="smtp.example.com", security="none", sender="owner@example.com")
        with self.assertRaises(ValueError):
            settings.validate()

    def test_ssl_transport_is_supported(self):
        settings = server.MailSettings(port=465, security="ssl", username="owner@example.com", password="secret", sender="owner@example.com")
        with patch.object(server.smtplib, "SMTP_SSL") as factory:
            factory.return_value.send_message.return_value = {}
            server.send_message(settings, "Visitor", "visitor@example.com", "Hello")
        self.assertIn("context", factory.call_args.kwargs)
        factory.return_value.starttls.assert_not_called()
        factory.return_value.login.assert_called_once_with("owner@example.com", "secret")

    def test_html_targets_web3forms_without_exposing_private_settings(self):
        result = request(self.app, method="GET", path="/")
        self.assertEqual(result["status"], 200)
        self.assertIn(b'action="https://api.web3forms.com/submit"', result["body"])
        self.assertNotIn(b"server-only-secret", result["body"])
        self.assertEqual(request(self.app, method="GET", path="/settings.json")["status"], 404)
        self.assertEqual(request(self.app, method="GET", path="/../settings.json")["status"], 404)
        head = request(self.app, method="HEAD", path="/")
        self.assertEqual(head["body"], b"")
        self.assertEqual(head["headers"]["Content-Length"], result["headers"]["Content-Length"])

    def test_unsupported_methods_are_rejected(self):
        result = request(self.app, method="GET")
        self.assertEqual(result["status"], 405)
        self.assertEqual(result["headers"]["Allow"], "POST")

    def test_web3forms_public_key_is_injected_without_exposing_smtp_password(self):
        settings = server.MailSettings(web3forms_access_key='key\"<script>alert(1)</script>', password="private-password")
        app = server.ContactApplication(settings, self.limiter)
        result = request(app, method="GET", path="/")
        self.assertIn(b'value="key&quot;&lt;script&gt;alert(1)&lt;/script&gt;"', result["body"])
        self.assertNotIn(b"private-password", result["body"])
        self.assertNotIn(b'<script>alert(1)</script>', result["body"])
        self.smtp_factory.assert_not_called()

    def test_csp_allows_browser_to_submit_to_web3forms(self):
        result = request(self.app, method="GET", path="/")
        csp = result["headers"]["Content-Security-Policy"]
        self.assertIn("connect-src 'self' https://api.web3forms.com;", csp)
        self.assertIn("form-action 'self' https://api.web3forms.com;", csp)
        self.assertNotIn("*", csp)

    def test_web3forms_key_environment_overrides_settings_file(self):
        root = Path(self.directory.name)
        (root / "settings.json").write_text(json.dumps({"WEB3FORMS_ACCESS_KEY": "file-key"}))
        with patch.object(server, "ROOT", root), patch.dict(server.os.environ, {}, clear=True):
            self.assertEqual(server.load_settings().web3forms_access_key, "file-key")
            with patch.dict(server.os.environ, {"WEB3FORMS_ACCESS_KEY": " environment-key "}):
                self.assertEqual(server.load_settings().web3forms_access_key, "environment-key")


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        self.wfile.write(b"220 localhost test mail sink\r\n")
        sender = ""
        recipients = []
        while True:
            line = self.rfile.readline(32768)
            if not line:
                return
            command = line.decode().strip()
            if command.upper().startswith(("EHLO", "HELO")):
                self.wfile.write(b"250-localhost\r\n250 SIZE 32768\r\n")
            elif command.upper().startswith("MAIL FROM:"):
                sender = command
                self.wfile.write(b"250 OK\r\n")
            elif command.upper().startswith("RCPT TO:"):
                recipients.append(command)
                self.wfile.write(b"250 OK\r\n")
            elif command.upper() == "DATA":
                self.wfile.write(b"354 End with a dot\r\n")
                lines = []
                while True:
                    line = self.rfile.readline(32768)
                    if line in (b".\r\n", b".\n", b""):
                        break
                    lines.append(line[1:] if line.startswith(b"..") else line)
                self.server.messages.put((sender, recipients, b"".join(lines)))
                self.wfile.write(b"250 Accepted\r\n")
            elif command.upper() == "QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return
            else:
                self.wfile.write(b"250 OK\r\n")


class QuietHTTPHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class DeliveryIntegrationTests(unittest.TestCase):
    def test_real_http_submission_reaches_local_smtp_server(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
            sink.daemon_threads = True
            sink.messages = queue.Queue()
            smtp_thread = threading.Thread(target=sink.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            smtp_thread.start()
            settings = server.MailSettings(host="127.0.0.1", port=sink.server_address[1], security="none", sender="portfolio@example.com")
            app = server.ContactApplication(settings, server.RateLimiter(Path(directory) / "limits.sqlite"))
            http = make_server("127.0.0.1", 0, app, handler_class=QuietHTTPHandler)
            http_thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            http_thread.start()
            try:
                url = f"http://127.0.0.1:{http.server_port}/api/contact"
                payload = {**MESSAGE, "name": "José Example", "message": "Hello Jason!\nLet’s discuss your portfolio."}
                outgoing = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                with urlopen(outgoing, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.load(response)["ok"])
                envelope_from, envelope_to, raw = sink.messages.get(timeout=1)
                message = BytesParser(policy=policy.default).parsebytes(raw)
                self.assertIn("portfolio@example.com", envelope_from)
                self.assertIn("jasone9661@gmail.com", envelope_to[0])
                self.assertEqual(str(message["Reply-To"]), "visitor@example.com")
                self.assertIn("José Example", str(message["Subject"]))
                self.assertIn(payload["message"], message.get_content().replace("\r\n", "\n"))
            finally:
                http.shutdown()
                http.server_close()
                sink.shutdown()
                sink.server_close()
                http_thread.join(timeout=2)
                smtp_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
