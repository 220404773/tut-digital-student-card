import os
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='dev-key-change-in-production')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default='localhost,127.0.0.1',
    cast=lambda v: [h.strip() for h in v.split(',') if h.strip()],
)

# DigitalOcean App Platform (and most PaaS hosts) terminate HTTPS at a load
# balancer and forward plain HTTP to the container - without this, Django's
# request.is_secure() (and so request.build_absolute_uri(), which the QR
# code URLs depend on) would think every request is insecure and generate
# http:// links even when the site is served over https://.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# The public address QR codes point to, e.g. https://your-app.ondigitalocean.app
# Optional: left blank, a QR code uses the address the page was opened on,
# which is wrong when a student views their card via another address
# (localhost, an old tunnel URL). QR codes are drawn on demand, so changing
# this updates every card's QR code at once.
SITE_URL = config('SITE_URL', default='')

# The same load balancer hides the client's IP address - see client_ip.py.
# Set one of these to match the host, never both blindly: a header the proxy
# doesn't actually set can be forged by any client.
CLIENT_IP_HEADER = config('CLIENT_IP_HEADER', default='')
TRUSTED_PROXY_COUNT = config('TRUSTED_PROXY_COUNT', default=0, cast=int)
RATELIMIT_IP_META_KEY = 'tut_student_card.client_ip.get_client_ip'

# How long page-visit rows and activity-log IP addresses are kept. IP
# addresses are personal information under POPIA, so they can't be held
# indefinitely; `manage.py purge_old_activity`, run daily, enforces this.
ACTIVITY_RETENTION_DAYS = config('ACTIVITY_RETENTION_DAYS', default=90, cast=int)

# Stops a browser from re-guessing a served file's type from its content and
# rendering it as something more dangerous than its declared Content-Type -
# the standard defence against a crafted "image" that's actually
# HTML/JS being executed in-browser when linked directly. Matters here
# specifically because profile_picture is served back to other users'
# browsers (dashboard, staff pages, the public verify page), not just the
# uploader's own. Django defaults this True already; set explicitly so it's
# a visible, deliberate control rather than an implicit framework default.
SECURE_CONTENT_TYPE_NOSNIFF = True

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 7  # 1 week to start; raise once confirmed working

# Idle session timeout: 10 minutes of inactivity signs a student out
# automatically - at the tighter end of the 5-15 minute range typical for a
# system handling personal student data (general authenticated apps run
# looser, 15-30 min; this leans toward the banking-style end given the PII
# involved). SESSION_SAVE_EVERY_REQUEST makes this a *rolling* idle timeout
# rather than a fixed one: the expiry resets on every authenticated request,
# so it only fires after real inactivity, not a fixed time after login. The
# client-side warning/extend prompt in base.html reads this same value via
# request.session.get_expiry_age(), and warns 2 minutes before expiry.
SESSION_COOKIE_AGE = config('SESSION_COOKIE_AGE', default=10 * 60, cast=int)
SESSION_SAVE_EVERY_REQUEST = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'crispy_forms',
    'crispy_bootstrap5',
    'csp',
    'accounts',
    'cards',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'csp.middleware.CSPMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'cards.middleware.PageVisitMiddleware',
]

ROOT_URLCONF = 'tut_student_card.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'tut_student_card.wsgi.application'
AUTH_USER_MODEL = 'accounts.User'

# Defaults to SQLite for local development. Set DB_ENGINE (and the other
# DB_* vars below) to switch to PostgreSQL - e.g. in production - without
# any code changes. See .env.example for the PostgreSQL values to set.
DATABASES = {
    'default': {
        'ENGINE': config('DB_ENGINE', default='django.db.backends.sqlite3'),
        'NAME': config('DB_NAME', default=os.path.join(BASE_DIR, 'db.sqlite3')),
        'USER': config('DB_USER', default=''),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default=''),
        'PORT': config('DB_PORT', default=''),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# WhiteNoise serves static files straight from the container, so there is no
# separate web server to configure. The compressed manifest backend gzips
# each file at collectstatic time and renames it with a content hash, which
# means responses are smaller and can be cached indefinitely - a new deploy
# changes the hash rather than needing a cache purge.
#
# Django's HashedFilesMixin skips the manifest lookup entirely while
# DEBUG is True, so local development still serves plain /static/css/style.css
# and needs no collectstatic run. Only DEBUG=False reads the manifest, and the
# Dockerfile runs collectstatic at build time to produce it. collectstatic also
# keeps an unhashed copy of every file, which is what lets manifest.json and
# sw.js keep referencing /static/icons/icon-192.png literally.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# Profile pictures and QR codes need durable storage - a PaaS container's
# local disk doesn't survive a redeploy and isn't shared across instances.
# Set AWS_STORAGE_BUCKET_NAME (a DigitalOcean Spaces bucket, which is
# S3-compatible) to switch media storage there; unset, local dev keeps
# using the filesystem exactly as before. Only the media ('default') backend
# changes - static files stay on WhiteNoise either way.
AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME', default='')
if AWS_STORAGE_BUCKET_NAME:
    AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default='')
    AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='')
    AWS_S3_ENDPOINT_URL = config('AWS_S3_ENDPOINT_URL', default='https://ams3.digitaloceanspaces.com')
    AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='ams3')
    AWS_DEFAULT_ACL = 'public-read'
    AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'}
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'

# Content-Security-Policy: the browser refuses to run or load anything not
# listed here, so injected markup can't execute script even if it slips past
# template escaping. Inline <script> blocks carry a per-request nonce
# ({{ request.csp_nonce }}); inline style attributes are still allowed, as the
# templates use them throughout and they can't run code.
# Bootstrap and Font Awesome are served from static/vendor/, so nothing needs
# a third-party origin. connect-src includes the media origin because the
# service worker (static/sw.js) re-fetches every request, profile photos
# included, and its fetches are checked against connect-src.
from csp.constants import NONCE, NONE, SELF, UNSAFE_INLINE

_MEDIA_ORIGINS = [AWS_S3_ENDPOINT_URL] if AWS_STORAGE_BUCKET_NAME else []
_CSP = {
    'DIRECTIVES': {
        'default-src': [SELF],
        'script-src': [SELF, NONCE],
        'style-src': [SELF, UNSAFE_INLINE],
        'font-src': [SELF],
        # data: is Bootstrap's own inline SVG icons (select arrows, checkboxes).
        'img-src': [SELF, 'data:', *_MEDIA_ORIGINS],
        'connect-src': [SELF, *_MEDIA_ORIGINS],
        'worker-src': [SELF],
        'manifest-src': [SELF],
        'object-src': [NONE],
        'base-uri': [SELF],
        'form-action': [SELF],
        'frame-ancestors': [NONE],
    },
}
# Report-only first: violations are logged in the browser console but nothing
# is blocked, so a missed resource shows up without breaking a page. Set
# CSP_REPORT_ONLY=False to enforce once the console stays clean.
if config('CSP_REPORT_ONLY', default=True, cast=bool):
    CONTENT_SECURITY_POLICY_REPORT_ONLY = _CSP
else:
    CONTENT_SECURITY_POLICY = _CSP

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# The cache holds rate-limit and failed-login counts and the email resend
# cooldown. It lives in the database rather than each process's memory, so
# counts are shared by every Gunicorn worker and survive a restart or deploy.
# MAX_ENTRIES is raised from Django's default of 300: at 300, ~150 failed
# logins for made-up emails pushed the real counters out and reset them.
# (Past MAX_ENTRIES Django culls a share of the oldest rows, so this is the
# point culling starts, not an exact size.)
# Needs `manage.py createcachetable` (run on container start; the test
# runner creates it itself).
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
        'OPTIONS': {'MAX_ENTRIES': 100_000},
    },
}

# Application logs (document-pipeline timing, which AI provider answered,
# failures) go to the console, which is what App Platform collects. Django
# otherwise drops INFO messages from the app's own loggers. Logs carry
# account ids, never document text, email addresses or keys.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
    },
    'loggers': {
        'accounts': {'handlers': ['console'], 'level': config('APP_LOG_LEVEL', default='INFO'), 'propagate': False},
        'cards': {'handlers': ['console'], 'level': config('APP_LOG_LEVEL', default='INFO'), 'propagate': False},
    },
}

# Map Django's "error" message tag to Bootstrap's "danger" class so error
# alerts actually get styled (Bootstrap has no alert-error class).
from django.contrib.messages import constants as message_constants
MESSAGE_TAGS = {
    message_constants.ERROR: 'danger',
}

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'home'

# TUT Branding Colors
TUT_BLUE = '#005DAA'
TUT_GOLD = '#FFCD00'
TUT_RED = '#E21E2C'

# Email (used for password reset). Defaults to printing emails to the
# console in development; set EMAIL_HOST etc. via environment variables
# to send real emails in production.
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@tut4life.ac.za')

# Only used when EMAIL_BACKEND is tut_student_card.azure_email_backend.AzureCommunicationEmailBackend
AZURE_EMAIL_CONNECTION_STRING = config('AZURE_EMAIL_CONNECTION_STRING', default='')
AZURE_EMAIL_SENDER_ADDRESS = config('AZURE_EMAIL_SENDER_ADDRESS', default='')

# Google Wallet - "Add to Google Wallet" button on the digital card page.
# GOOGLE_WALLET_CREDENTIALS_JSON is the full service account key file's
# contents (client_email + private_key are read out of it), not a path -
# App Platform env vars can't reference a file on disk.
GOOGLE_WALLET_ISSUER_ID = config('GOOGLE_WALLET_ISSUER_ID', default='')
GOOGLE_WALLET_CREDENTIALS_JSON = config('GOOGLE_WALLET_CREDENTIALS_JSON', default='')

# OCR Settings
OCR_ENABLED = True
OCR_CONFIDENCE_THRESHOLD = 0.8
# AI document extraction runs on our own Azure OpenAI resource in South Africa
# North. Leave these unset and extraction is rule-based only, with no document
# text sent anywhere. The deployment type (set in the Foundry portal) decides
# where processing happens: a regional "Standard" deployment keeps it in South
# Africa; "Global Standard" or "Data Zone" deployments can process elsewhere.
AZURE_OPENAI_ENDPOINT = config('AZURE_OPENAI_ENDPOINT', default='')
AZURE_OPENAI_API_KEY = config('AZURE_OPENAI_API_KEY', default='')
AZURE_OPENAI_DEPLOYMENT = config('AZURE_OPENAI_DEPLOYMENT', default='')
AZURE_OPENAI_API_VERSION = config('AZURE_OPENAI_API_VERSION', default='2025-04-01-preview')
# 'minimal' is the fastest setting for GPT-5 models; leave blank for models
# that don't accept a reasoning_effort parameter.
AZURE_OPENAI_REASONING_EFFORT = config('AZURE_OPENAI_REASONING_EFFORT', default='minimal')

# Fallback, used only when the Azure call fails. NVIDIA processes outside South
# Africa with no data processing agreement in place, so this is a cross-border
# transfer under POPIA s72 without that protection. Leave blank to fall back
# to rule-based extraction instead.
NVIDIA_API_KEY = config('NVIDIA_API_KEY', default='')
# NVIDIA retires hosted models (meta/llama-3.1-8b-instruct ended 26 Aug 2026),
# so check this still answers before relying on it.
NVIDIA_MODEL = config('NVIDIA_MODEL', default='nvidia/nemotron-3-super-120b-a12b')

# Document verification proves a proof-of-registration document was uploaded
# and could be read - it cannot prove the uploader is the person named on it,
# because nothing checks the student number against an institutional record.
# Requiring email confirmation first closes the most practical version of that
# gap: the account email is {student_number}@tut4life.ac.za, so confirming it
# proves control of the mailbox tied to that student number.
# Set False only for an offline demo where inbox access isn't available.
REQUIRE_EMAIL_CONFIRMATION_FOR_CARD = config(
    'REQUIRE_EMAIL_CONFIRMATION_FOR_CARD', default=True, cast=bool
)
TESSERACT_CMD = config('TESSERACT_CMD', default='')
