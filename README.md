# Jason Escalante's portfolio

A responsive portfolio with a simple introduction, scroll-in project cards for
ClearFrame and Pokémon Card Manager, and FantasyIQ marked as in progress.

This includes the complete portfolio, the rounded Name / Email / Message form,
and a Python backend that sends messages to **jasone9661@gmail.com** over SMTP.
The visitor's email becomes **Reply-To**, so replying in Gmail goes back to them.

**Live Gmail delivery is not activated in this download.** Configure your own
server-side email credential, then run or deploy the Python application.
No Gmail password or API key is included. The bundled tests deliver to a local
SMTP test server; they do not contact Gmail.

**DigitalOcean email status:** this version uses SMTP. DigitalOcean blocks common
SMTP ports on Droplets and restricts SMTP on App Platform. The portfolio can be
deployed there, but sending email needs an HTTPS email-provider integration first.
Setting a Gmail App Password alone does not resolve the platform restriction.
The direct email link remains available while the form is unconfigured.

## Run it locally

1. Extract the ZIP and open a terminal inside `portfolio-contact-python`.
2. Use Python 3.10 or newer.
3. Copy `settings.example.json` to **`settings.json`** in this folder.
4. Fill in `SMTP_PASSWORD` privately in `settings.json`. For the included Gmail
   configuration, use an App Password for your sending account. Leave the
   `CONTACT_TO` value as `jasone9661@gmail.com`.
5. Run:

   ```text
   python server.py
   ```

6. Open **http://127.0.0.1:8000** in your browser and use the form.

Google App Passwords require 2-Step Verification and are not available for every
account. Create one through your Google account's
[App Passwords page](https://myaccount.google.com/apppasswords), if available.
Use the App Password rather than your normal Google password. Enter its
characters without the grouping spaces. Keep `settings.json` private and out of
Git; the included `.gitignore` excludes it. The server never serves this file.

Gmail's preset uses `smtp.gmail.com`, port `587`, with STARTTLS. Another mail
provider can be configured using its SMTP host, port, credentials, and approved
sender address. SSL from connection start is supported with
`SMTP_SECURITY` set to `ssl`, usually on port `465`.

Environment variables with the same names as the settings keys override the
JSON file. A hosting provider's private environment-variable settings are the
preferred place for live credentials. This application does not read `.env`
files automatically. Restart the server after changing configuration.

## Deploy the website on DigitalOcean App Platform

1. In DigitalOcean, select **Create App**, connect GitHub, and select
   `jasone9/Portfolio`, branch `main`.
2. Choose a Python **Web Service** and keep the source directory at the repository
   root. The HTML and backend are served together by Python.
3. Use these settings:

   | Setting | Value |
   | --- | --- |
   | Build command | `pip install -r requirements.txt` |
   | Run command | `waitress-serve --listen=0.0.0.0:8080 server:application` |
   | HTTP port | `8080` |
   | Route | `/` |

4. Deploy. DigitalOcean provides an HTTPS address for the site.
5. Set `PUBLIC_ORIGIN` in the service's environment variables to that exact
   address, such as `https://your-app.ondigitalocean.app`, and redeploy. If you
   later use your own domain, update this setting to the origin visitors use.

This publishes the portfolio, not live email delivery. Do not add Gmail
credentials expecting SMTP to work on DigitalOcean; the email transport needs
to be adapted to a provider's HTTPS API. Keep any future API key in the service's
encrypted environment variables, never in HTML or GitHub.

The SQLite limiter is temporary application state. On App Platform, its counts
reset when the container is replaced, and they are not shared across instances.
Use a single instance for this version; shared rate limiting is needed before
scaling the contact backend.

References: [Create an app](https://docs.digitalocean.com/products/app-platform/how-to/create-apps/),
[App Platform limits](https://docs.digitalocean.com/products/app-platform/details/limits/),
and [DigitalOcean's SMTP restrictions](https://docs.digitalocean.com/support/why-is-smtp-blocked/).

## Deployment on a host that permits SMTP

The Python backend must be running for email delivery. Opening `index.html`
directly or uploading it to a static-only host displays the form but does not
send email. The raw HTML deliberately leaves its contact endpoint inactive;
the Python server enables it when serving the page. Missing credentials and
SMTP failures produce an error, never a successful-send message.

For a public website, use a Python-capable host and a production WSGI server.
The local `python server.py` command is for development. Waitress supports
Windows and Linux:

```text
python -m pip install -r requirements.txt
waitress-serve --listen=127.0.0.1:8000 server:application
```

This command binds to localhost for use behind your host's HTTPS reverse proxy.
Use the listen address and port required by your hosting provider. Set
`PUBLIC_ORIGIN` to the exact public origin, such as `https://your-domain.example`,
without a trailing path. This is used to check browser submissions through the
proxy. Use HTTPS for the public page.

The rate limiter uses `REMOTE_ADDR`. If the host puts the app behind a proxy,
configure Waitress to trust only the host's actual proxy addresses and appropriate
forwarded headers. Do not blindly trust arbitrary client-supplied forwarding
headers. Keep the `instance/` directory writable. Multiple workers on one server
can share the local SQLite rate-limit file; multiple servers need shared rate
limiting at the host or application layer.

The server has a fixed configured recipient, input limits, header-injection
checks, a hidden bot field, a five-attempts-per-ten-minutes IP limit, and bounded
SMTP timeouts. Remote SMTP connections require TLS. Unencrypted SMTP is allowed
only for the loopback test server. Successful submission means the SMTP server
accepted the email; inbox placement is still handled by your mail provider.
Check Spam when testing live delivery.

## Files

- `index.html`: the portfolio and accessible, responsive contact form.
- `server.py`: page serving, validation, rate limiting, and email delivery.
- `settings.example.json`: a blank credential template for Gmail SMTP.
- `requirements.txt`: Waitress for production hosting; local development and
  the Python tests use only the standard library.
- `tests/test_contact.py`: validation, transport, and end-to-end delivery tests.

## Test without sending real email

```text
python -m unittest discover -s tests -v
```

The integration test starts a local HTTP server and a local SMTP inbox, submits
a contact message over HTTP, and verifies the received email, recipient, Reply-To,
subject, and Unicode body. Other tests cover error responses, missing credentials,
rate limits, TLS failures, and keeping private settings out of page responses.

## Configuration references

- [Google App Passwords](https://support.google.com/accounts/answer/185833)
- [Gmail SMTP settings](https://support.google.com/mail/answer/7104828)
- [Python SMTP client](https://docs.python.org/3/library/smtplib.html)
- [Waitress deployment](https://docs.pylonsproject.org/projects/waitress/en/stable/usage.html)
