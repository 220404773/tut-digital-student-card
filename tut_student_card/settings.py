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
    'accounts',
    'cards',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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
AUTH_USER_MODEL = 'accounts.StudentUser'

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

# Profile pictures and QR codes need durable storage - a PaaS container's
# local disk doesn't survive a redeploy and isn't shared across instances.
# Set AWS_STORAGE_BUCKET_NAME (a DigitalOcean Spaces bucket, which is
# S3-compatible) to switch media storage there; unset, local dev keeps
# using the filesystem exactly as before.
AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME', default='')
if AWS_STORAGE_BUCKET_NAME:
    AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default='')
    AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='')
    AWS_S3_ENDPOINT_URL = config('AWS_S3_ENDPOINT_URL', default='https://ams3.digitaloceanspaces.com')
    AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='ams3')
    AWS_DEFAULT_ACL = 'public-read'
    AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}
    STORAGES = {
        'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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
NVIDIA_API_KEY = config('NVIDIA_API_KEY', default='')

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
