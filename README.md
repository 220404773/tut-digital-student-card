# TUT Digital Student Card System

A digital student ID card system for Tshwane University of Technology. Students sign up with a proof of registration document, get it read automatically via OCR + an LLM classifier, and receive a digital card with a QR code — plus, optionally, an "Add to Google Wallet" pass.

**Live deployment**: DigitalOcean App Platform + Managed PostgreSQL + Spaces (media) + Azure Communication Services (email) + Google Wallet (demo mode). 

## Features

- **Signup with document verification** — student number + proof of registration (PDF/JPG/PNG), read via Tesseract OCR and classified/extracted by Azure OpenAI (South Africa North), with NVIDIA as a fallback only when Azure is configured but unavailable
- **Digital card** — issued once the student is verified, has confirmed their student email and has added a profile photo, with a unique card number and a QR code drawn on demand. After issue, a new photo or name waits for staff approval before it appears on the card
- **Email confirmation** — a separate identity check from OCR verification, requiring an explicit button click (not just visiting the link) so email security scanners can't silently consume it
- **Google Wallet** — "Add to Google Wallet" button, signed via a service account key, no custom branding required
- **Public card verification** — scanning the QR code (or the Wallet pass) opens an unauthenticated page showing card validity, styled to match the card itself; an invalid/expired card is deliberately styled differently (red, not blue) so it reads as alarming to whoever scans it
- **Admin reporting dashboard** — signup performance, OCR accuracy/confidence, email confirmation rate and time-to-confirm, site traffic, all with drill-down detail pages
- **Rate limiting** on signup, OCR preview, password reset and public verification, and failed-login protection per email and network (see *Rate limiting* below)
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
│   ├── utils.py                                  # OCR pipeline, LLM classification, QR drawing, the card-issue rule
│   ├── google_wallet.py                          # Builds the signed "Add to Google Wallet" save URL
│   ├── admin.py                                  # StudentCardAdmin (auto-generates card_number, backfill action)
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
# Edit .env - set the AZURE_OPENAI_* values for AI document classification (NVIDIA_API_KEY
# optionally, as a fallback). Without them, documents are classified by the rule-based
# checker instead. Everything else has a working local default (SQLite, local media/
# folder, console email backend).

python manage.py collectstatic --noinput   # tests and DEBUG=False read the static manifest

python manage.py migrate
python manage.py createcachetable   # the database cache (rate-limit and login counts)
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

Tests cover signup/login, email confirmation (including that a bare link visit does *not* confirm, only the button click), password reset, file upload validation, document classification and the AI fallback order, student-number and email identity checks, staff review requests, rate limits, QR codes, card expiry, the Content-Security-Policy, and staff/student permission boundaries.

## Configuration

Every environment variable the app reads is documented with comments in **`.env.example`** — copy it to `.env` and fill in what you need. Nothing is required for local development: without the `AZURE_OPENAI_*` values the rule-based classifier is used, which affects classification quality, not whether the app runs.

For production values (DigitalOcean Managed Database, Spaces, Azure Communication Services, Google Wallet)

## How it works

### Signup flow

1. Student uploads a proof-of-registration document (PDF/JPG/PNG, max 5MB, validated against actual file content, not just the extension)
2. Text is extracted directly (PDFs with a real text layer) or via Tesseract OCR (scanned/photographed documents)
3. ID numbers are removed from the text, then Azure OpenAI (`gpt-5-nano`, South Africa North) classifies whether it's genuinely a TUT proof of registration and extracts student number/name/dates in one call. If Azure is configured but unavailable (unreachable, timed out, rate-limited or a server error), NVIDIA's hosted API is tried within the same 20-second budget. NVIDIA is never used on its own, and never after Azure has answered - even with an error or a malformed reply. If no model answers, a rule-based classifier takes over. Text that appears to give instructions to the AI is never sent to a model: a person reviews it instead
4. A document is accepted only when the model says it is a TUT proof of registration *and* its confidence is at least 0.8, and the student number on it must match the one entered. The card is issued once the student confirms their `{student_number}@tut4life.ac.za` email and adds a profile photo - the photo is what campus staff compare with the holder's face, so after issue a new photo or name waits for a staff member to approve it (Review Requests page)
5. Anything the system can't verify on its own (a student-number mismatch, a borderline score, a document that tried to instruct the AI, or a rejection the student disputes) goes to a staff **Review Requests** queue, where a person makes the decision (POPIA s71)
6. The uploaded document is deleted after processing regardless of outcome (POPIA) — including on every code path, not just the success path
7. A confirmation email is sent separately — this verifies the student controls the email address, independent of the document check
8. The card's QR code is drawn from its card number each time it's shown (never stored), pointing at `SITE_URL` when set

### Manual (admin) verification

A staff member can tick "is_verified" on a student directly in Django admin — this is treated as an equivalent manual approval: it closes any pending review request and issues the card under the same rule as every other path (once the student has confirmed their email). Every path issues the first card through `issue_card_if_allowed()` in `cards/utils.py`.

### Rate limiting

Limits are counted in Django's cache. A cache can live in two places:

- **In-process memory** (Django's default): each Gunicorn worker keeps its own counts. With several workers the real limit becomes the limit times the number of workers, and every restart or deploy clears all counts. It also holds only 300 entries, so a flood of junk requests can push the real counters out.
- **A shared store** that every process reads and writes. This project uses **Django's database cache** (`CACHES` in `settings.py`, table created by `manage.py createcachetable` on container start): counts are shared by all workers and survive restarts, and old entries are only culled once the table passes 100,000 (Django's default limit is 300). It costs nothing extra and keeps the counts (which include IP addresses) in the app's own database. Redis would do the same job faster if traffic ever grew that large.

Login is protected per **email and network** rather than per network alone (`accounts/login_guard.py`): only failed logins count, and after 5 that pair waits 30 seconds, doubling up to 15 minutes. A campus sharing one IP address isn't locked out together, and someone typing junk at a student's address only slows their own network. 50 failures for one email within a clock hour, from anywhere, pause that account for 15 minutes. A new pause can't start within 60 minutes of the last one, so the worst a determined attacker can do is a 15-minute lockout in any hour, in exchange for capping guesses at 50 an hour.

## Deployment

Live on DigitalOcean App Platform. The full explicit runbook — including exact `doctl` commands, the Azure Communication Services email setup, the Google Wallet setup, environment variable list, teardown steps, rather than being duplicated here, so there's one source of truth instead of two that can drift apart.

## Known limitations

- No third-party adversarial security review — hardening so far came from real bugs found via hands-on testing against the live deployment plus a dependency vulnerability scan, not a penetration test
- Someone with several networks can still pause one student's login for up to 15 minutes an hour (see *Rate limiting*)
- The prompt-injection check is a pattern match: a reworded instruction can get past it, so the student-number match, email confirmation and staff review are what limit the damage
- Document checks can't confirm a student against the university's own records (no access to iEnabler), so a convincing forged document is caught only by the student-number match, email confirmation and staff review
- Google Wallet is in Demo Mode (test accounts only, "[TEST ONLY]" watermark) — going further would need Google's business verification review for publishing access


