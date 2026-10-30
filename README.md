# Jason Escalante's portfolio

A responsive portfolio with a simple introduction, scroll-in cards for ClearFrame
and Pokémon Card Manager, and FantasyIQ marked as in progress.

## Contact form: Web3Forms

The form now submits **directly from the visitor's browser** to
`https://api.web3forms.com/submit` over HTTPS. This avoids DigitalOcean's SMTP
restrictions and follows Web3Forms' browser-side integration for its free plan.
Python still serves the website. A local configuration can override the embedded
public form key when needed.
The server's Content Security Policy explicitly allows the Web3Forms API.

**The owner-provided public Web3Forms access key is configured in `index.html`.**
After pulling the code and restarting the service, the form can submit using that
key without a settings file. The form must be verified for **jasone9661@gmail.com**
in [Web3Forms](https://app.web3forms.com/). Live inbox delivery still needs to be
confirmed with a real browser submission.
The destination inbox is determined by the access key, not an editable `to` field.
The visitor's `email` field becomes Reply-To, so replying reaches the visitor.

Web3Forms documents its form access key as public and suitable for client-side
HTML; it is not a Gmail password or a private account API token. Only use the
form's **access key** here. No Gmail App Password is needed for this form.

The form includes a hidden bot-check checkbox, native required/email/length
validation, a pending state that blocks duplicate clicks, and a 15-second timeout.
It displays success only after an HTTP success response with `success: true`.
Failed submissions retain the visitor's message and offer a direct email link.
Web3Forms handles server-side anti-spam and submission limits; the old Python
SMTP rate limiter does not apply to browser-to-Web3Forms requests.

## Update the existing DigitalOcean Droplet

The existing Nginx proxy, HTTPS certificate, and `portfolio.service` remain usable.

1. In the **Droplet console**, update the code:

   ```bash
   sudo git -C /opt/portfolio pull --ff-only
   ```

2. Restart the running Python service so it reloads the HTML and settings:

   ```bash
   sudo systemctl restart portfolio
   sudo systemctl status portfolio --no-pager
   ```

3. Open **https://jasonescalante.com**, refresh the page, and send one test message
   through the form. Confirm it appears in Gmail and check Spam if needed.
   The automated tests do not verify live inbox delivery.

Optional: to use a different public form key, add `WEB3FORMS_ACCESS_KEY` to the
existing `settings.json` or the service's environment. A nonempty local key
replaces the embedded key in the served page; a blank or absent setting preserves
the embedded key. Restart the service after changing configuration. Keep any
existing settings when editing the JSON file.

A GitHub push alone does not update this manually installed Droplet. Pulling the
changes and restarting the service are required. If the form says it is not
configured, confirm the key belongs to the receiving email and restart the
service after changing settings. If you set `WEB3FORMS_ACCESS_KEY` as a systemd
environment variable, it overrides the JSON file; remove any stale override.

Because submission happens directly in the browser, it works with both the root
and `www` HTTPS origins without proxy-origin configuration for the contact form.
Web3Forms' free plan is not intended for relaying submissions from Python or PHP;
server-side usage requires a paid plan and server IP allowlisting.

## Run locally or use another Python host

Use Python 3.10 or newer. The embedded public key is used by default. To override
it, configure `WEB3FORMS_ACCESS_KEY` in `settings.json` or the environment.
Run `python server.py` and open http://127.0.0.1:8000. This is a development server.

For production, install `requirements.txt` and run Waitress behind an HTTPS proxy:

```text
waitress-serve --listen=127.0.0.1:8000 server:application
```

On DigitalOcean App Platform, use a Python Web Service with build command
`pip install -r requirements.txt`, run command
`waitress-serve --listen=0.0.0.0:8080 server:application`, HTTP port `8080`, and
optionally override `WEB3FORMS_ACCESS_KEY` in the component's environment variables.

For static hosting, fill the hidden `access_key` input directly in `index.html`.
No Python backend is required for Web3Forms itself. A blank or placeholder key
is rejected locally rather than displaying a misleading success message.
If configuring your own CSP, permit `https://api.web3forms.com` in `connect-src`
and `form-action`.

The original `/api/contact` SMTP endpoint remains for backwards compatibility,
but the current form does not call it. Its optional SMTP settings are retained
in `settings.example.json`. Keep any SMTP passwords private. Missing SMTP
credentials do not prevent the website or Web3Forms integration from running.

## Tests

Run from the repository root:

```text
python -m unittest discover -s tests -v
node --test tests/test_web3forms.cjs
```

The Python tests cover public-key configuration and safe HTML insertion, CSP,
page serving, and the legacy SMTP validation/delivery contract using a local mail
sink. The Node tests run the actual form handler with browser APIs stubbed to
check the Web3Forms payload, missing keys, bot checks, duplicate submissions,
provider errors, network failures, timeout, and preserving inputs on failure.
No automated test contacts Web3Forms or sends real email. GitHub Actions runs
both suites on pushes and pull requests with Python 3.10/3.13 and Node 22.

## References

- [Web3Forms API and browser-side requirement](https://docs.web3forms.com/getting-started/api-reference)
- [HTML and JavaScript integration](https://docs.web3forms.com/how-to-guides/html-and-javascript)
- [Public form access key and setup](https://web3forms.com/)
- [Troubleshooting](https://docs.web3forms.com/getting-started/troubleshooting)
