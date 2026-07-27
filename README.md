# TUT Digital Student Card System

A digital student ID card system for Tshwane University of Technology. Students sign up with a proof-of-registration document, get it read automatically via OCR + an LLM classifier, and receive a digital card with a QR code — plus, optionally, an "Add to Google Wallet" pass.

**Live deployment**: DigitalOcean App Platform + Managed PostgreSQL + Spaces (media) + Azure Communication Services (email) + Google Wallet (demo mode). See the [Deployment & Risk Brief](https://claude.ai/code/artifact/47e5bcfe-7355-4e82-b904-ddd74a05f35f) for the full rebuild-from-zero runbook, teardown steps, cost breakdown, and every risk finding closed or open.

## Features

- **Signup with document verification** — student number + proof of registration (PDF/JPG/PNG), read via Tesseract OCR and classified/extracted by an NVIDIA-hosted LLM
- **Digital card** — auto-generated on verification, with a unique card number and QR code
- **Email confirmation** — a separate identity check from OCR verification, requiring an explicit button click (not just visiting the link) so email security scanners can't silently consume it
- **Google Wallet** — optional "Add to Google Wallet" button, signed via a service account key, no custom branding required
- **Public card verification** — scanning the QR code (or the Wallet pass) opens an unauthenticated page showing card validity, styled to match the card itself; an invalid/expired card is deliberately styled differently (red, not blue) so it reads as alarming to whoever scans it
- **Admin reporting dashboard** — signup performance, OCR accuracy/confidence, email confirmation rate and time-to-confirm, site traffic, all with drill-down detail pages
- **Rate limiting** on login, signup, OCR preview, password reset, and public verification
- **Simplified Django admin** — staff accounts are just `is_staff` + `is_superuser` (no Groups/permissions management); ticking "is_verified" on a student is a manual/soft approval that auto-generates their card the same way OCR verification does; staff+superuser accounts get an auto-assigned placeholder student number (`STAFF-ADMIN-1`, `-2`, ...) since they aren't real students

## Project Structure

```
tut_student_card/
├── accounts/                                    # Auth, signup, profile, email confirmation
│   ├── models.py                                # StudentUser (extends AbstractUser)
│   ├── forms.py                                  # Signup/login/profile forms, magic-byte file validation
│   ├── views.py                                  # Signup, login, profile, email confirm/resend, password reset wrapper
│   ├── admin.py                                  # Simplified StudentUserAdmin (is_staff/is_superuser only)
│   ├── tokens.py                                  # Email confirmation token generator
│   └── templates/accounts/                        # Signup, login, profile, confirm-email landing page, password reset
├── cards/                                        # Digital card, verification, admin reporting
│   ├── models.py                                 # StudentCard, CardActivity, SignupMetrics, OCRMetrics, PageVisit
│   ├── views.py                                  # Card display, QR download, public/private verification, admin reports
│   ├── utils.py                                  # OCR pipeline, LLM classification, QR/card generation
│   ├── google_wallet.py                          # Builds the signed "Add to Google Wallet" save URL
│   ├── admin.py                                  # StudentCardAdmin (auto-generates card_number/QR, backfill action)
│   └── templates/cards/                           # Dashboard, card, verification pages, admin reports
├── tut_student_card/
│   ├── settings.py                                # All configuration, env-var driven
│   ├── azure_email_backend.py                     # Custom Django email backend (Azure Communication Services REST API)
│   ├── urls.py
│   ├── asgi.py / wsgi.py
├── templates/
│   ├── base.html                                  # Shared layout, navbar, password toggle/live-match JS
│   └── index.html
├── static/js/script.js                            # OCR auto-fill, password toggle, live password match
├── manage.py
├── requirements.txt
├── Dockerfile                                     # Used for both local Docker and the DigitalOcean deploy
├── .env.example                                   # Every environment variable the app reads, with comments
└── README.md
```

## Local development

### Prerequisites
- Python 3.12
- Tesseract OCR (`apt-get install tesseract-ocr` on Linux, or the [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki)) — optional; without it, image uploads (not PDFs with an embedded text layer) won't extract text
- SQLite is the default local database — no setup needed

### Setup

```bash
git clone https://github.com/220404773/tut-digital-student-card.git
cd tut-digital-student-card

python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # macOS/Linux

pip install -r requirements.txt

cp .env.example .env
# Edit .env - at minimum set NVIDIA_API_KEY if you want OCR classification to work;
# everything else has a working local default (SQLite, local media/ folder, console email backend)

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Home: http://localhost:8000/
- Admin: http://localhost:8000/admin/
- Sign up: http://localhost:8000/accounts/signup/

With no further configuration: email "sends" print to the console instead of a real inbox, media files save to a local `media/` folder instead of cloud storage, and the Google Wallet button simply doesn't appear (the app checks for those credentials and no-ops if they're missing).

### Running with Docker

The same `Dockerfile` used for the production deploy works locally:

```bash
docker build -t tut-student-card .
docker run -p 8080:8080 --env-file .env tut-student-card
```

### Tests

```bash
python manage.py test
```

56 tests covering signup/login, email confirmation (including that a bare link visit does *not* confirm, only the button click), password reset, file upload validation, OCR classification thresholds, card expiry, admin card auto-generation, the STAFF-ADMIN placeholder numbering, and staff/student permission boundaries.

## Configuration

Every environment variable the app reads is documented with comments in **`.env.example`** — copy it to `.env` and fill in what you need. Nothing is required for local development beyond `NVIDIA_API_KEY` (and even that only affects OCR classification quality, not whether the app runs).

For production values (DigitalOcean Managed Database, Spaces, Azure Communication Services, Google Wallet), see the [Deployment & Risk Brief](https://claude.ai/code/artifact/47e5bcfe-7355-4e82-b904-ddd74a05f35f) — it has the exact setup steps for each, in the order that avoids the dead ends already hit once (the free App Platform "Dev Database" and SendGrid/Brevo's email-authentication problems, specifically).

## How it works

### Signup flow

1. Student uploads a proof-of-registration document (PDF/JPG/PNG, max 5MB, validated against actual file content, not just the extension)
2. Text is extracted directly (PDFs with a real text layer) or via Tesseract OCR (scanned/photographed documents)
3. An NVIDIA-hosted LLM (`meta/llama-3.1-8b-instruct`) classifies whether it's genuinely a TUT proof of registration and extracts student number/name/dates in one call; a regex-based fallback classifier runs if the LLM is unavailable
4. If confidence is high enough, the account is auto-verified and a digital card (unique card number + QR code) is generated immediately
5. The uploaded document is deleted after processing regardless of outcome (POPIA) — including on every code path, not just the success path
6. A confirmation email is sent separately — this verifies the student controls the email address, independent of the document check

### Manual (admin) verification

A staff member can tick "is_verified" on a student directly in Django admin — this is treated as an equivalent manual approval and generates the card the same way, so there's no separate "generate card" step to remember.

## Deployment

Live on DigitalOcean App Platform. The full explicit runbook — including exact `doctl` commands, the Azure Communication Services email setup (and why it replaced Brevo/SendGrid), the Google Wallet setup, environment variable list, teardown steps, and cost breakdown — lives in the **[Deployment & Risk Brief](https://claude.ai/code/artifact/47e5bcfe-7355-4e82-b904-ddd74a05f35f)** rather than being duplicated here, so there's one source of truth instead of two that can drift apart.

## Known limitations

- No third-party adversarial security review — hardening so far came from real bugs found via hands-on testing against the live deployment plus a dependency vulnerability scan, not a penetration test
- Rate limiting uses Django's default in-process cache, correct for the current single-instance deployment but wouldn't hold across multiple instances without switching to a shared cache backend
- Google Wallet is in Demo Mode (test accounts only, "[TEST ONLY]" watermark) — going further would need Google's business verification review for publishing access

See the Deployment & Risk Brief's risk findings section for the full list of what's been found and fixed, with the reasoning behind each.
