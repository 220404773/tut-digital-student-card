"""
Google Wallet passes for students' digital cards.

build_google_wallet_save_url(): the "Add to Google Wallet" link. Uses the
JWT-with-embedded-objects pattern: the class and object definitions are
embedded directly in a signed JWT rather than pre-created via a separate
REST API call to Google.

sync_wallet_pass() / deactivate_wallet_pass(): once a student has added the
pass, Wallet keeps showing whatever it was given - so when a card is revoked,
reissued or expires, or the name or expiry date changes, the pass is updated
through the Wallet REST API (a service-account OAuth token, made with the
same key). A revoked or replaced card then shows as inactive in Wallet too,
not just when its QR code is scanned. A student who never added the pass has
nothing to update: Google answers 404 and that is fine.

All of it is a no-op until GOOGLE_WALLET_ISSUER_ID and
GOOGLE_WALLET_CREDENTIALS_JSON are set. A failure is logged, never raised:
the card itself has already changed, and its QR code always shows the truth.

The pass is TUT blue with the app's logo (when SITE_URL gives a public
address Google can fetch it from), and its front shows the student number
and expiry date. The front layout belongs to the class, which Google keeps
as first created: update_wallet_class() (the sync_google_wallet command, run
after each deploy) brings it, and every saved pass, up to date. No student
photo is put on the pass.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from django.conf import settings
from django.core.cache import cache
from django.templatetags.static import static

logger = logging.getLogger(__name__)

WALLET_API = 'https://walletobjects.googleapis.com/walletobjects/v1/genericObject/'
WALLET_CLASS_API = 'https://walletobjects.googleapis.com/walletobjects/v1/genericClass/'
TUT_BLUE = '#005DAA'
WALLET_SCOPE = 'https://www.googleapis.com/auth/wallet_object.issuer'
# How each card status shows in Wallet.
WALLET_STATES = {'active': 'ACTIVE', 'expired': 'EXPIRED', 'revoked': 'INACTIVE'}
TIMEOUT_SECONDS = 8


def _load_credentials():
    if not settings.GOOGLE_WALLET_CREDENTIALS_JSON or not settings.GOOGLE_WALLET_ISSUER_ID:
        return None
    return json.loads(settings.GOOGLE_WALLET_CREDENTIALS_JSON)


def _object_id(card_number):
    return f"{settings.GOOGLE_WALLET_ISSUER_ID}.{card_number}"


def _class_id():
    return f"{settings.GOOGLE_WALLET_ISSUER_ID}.tut_digital_student_card"


def _text(value):
    return {"defaultValue": {"language": "en-US", "value": value}}


def _pass_class():
    """The pass type: its front shows the student number and the expiry date."""
    def item(field_id):
        return {"firstValue": {"fields": [{"fieldPath": f"object.textModulesData['{field_id}']"}]}}

    return {
        "id": _class_id(),
        "classTemplateInfo": {"cardTemplateOverride": {"cardRowTemplateInfos": [
            {"twoItems": {"startItem": item('student_number'), "endItem": item('valid_until')}},
        ]}},
    }


def _logo():
    """The app icon, if the site has a public address Google can fetch it from."""
    if not settings.SITE_URL:
        return {}
    uri = settings.SITE_URL.rstrip('/') + static('icons/icon-192.png')
    return {"logo": {"sourceUri": {"uri": uri}, "contentDescription": _text("TUT Student Card")}}


def _pass_details(student_card):
    """What the pass shows - the same for the save link and every update."""
    user = student_card.user
    return {
        "state": WALLET_STATES.get(student_card.status, 'INACTIVE'),
        "hexBackgroundColor": TUT_BLUE,
        **_logo(),
        "cardTitle": _text("TUT Digital Student Card"),
        "subheader": _text("Student"),
        "header": _text(user.get_full_name_display()),
        "textModulesData": [
            {"header": "Student Number", "body": user.student_number, "id": "student_number"},
            {"header": "Card Number", "body": student_card.card_number, "id": "card_number"},
            {
                "header": "Valid Until",
                "body": student_card.expiry_date.strftime('%Y-%m-%d') if student_card.expiry_date else "-",
                "id": "valid_until",
            },
        ],
    }


def build_google_wallet_save_url(student_card, verification_url):
    """Returns a https://pay.google.com/gp/v/save/<jwt> URL the student can
    click to add their card to Google Wallet, or None if Google Wallet
    credentials aren't configured."""
    credentials = _load_credentials()
    if not credentials:
        return None

    generic_class = _pass_class()
    generic_object = {
        "id": _object_id(student_card.card_number),
        "classId": _class_id(),
        **_pass_details(student_card),
        "barcode": {"type": "QR_CODE", "value": verification_url},
    }

    payload = {
        "iss": credentials["client_email"],
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "payload": {
            "genericClasses": [generic_class],
            "genericObjects": [generic_object],
        },
    }

    token = jwt.encode(payload, credentials["private_key"], algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{token}"


def _access_token(credentials):
    """A Wallet API access token for the service account, reused while valid."""
    cache_key = 'google-wallet-access-token'
    token = cache.get(cache_key)
    if token:
        return token
    now = int(time.time())
    token_uri = credentials.get('token_uri', 'https://oauth2.googleapis.com/token')
    assertion = jwt.encode(
        {"iss": credentials["client_email"], "scope": WALLET_SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600},
        credentials["private_key"], algorithm="RS256",
    )
    body = urllib.parse.urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(token_uri, data=body), timeout=TIMEOUT_SECONDS) as response:
        data = json.loads(response.read())
    # Kept a little under its lifetime so it never expires mid-request.
    cache.set(cache_key, data['access_token'], max(60, int(data.get('expires_in', 3600)) - 300))
    return data['access_token']


def _patch_pass(card_number, changes):
    """Apply changes to the student's pass, if they added one. True if updated."""
    credentials = _load_credentials()
    if not credentials:
        return False
    try:
        request = urllib.request.Request(
            WALLET_API + urllib.parse.quote(_object_id(card_number)),
            data=json.dumps(changes).encode(), method='PATCH',
            headers={'Authorization': f'Bearer {_access_token(credentials)}', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:  # the student never added this card to Wallet
            return False
        logger.warning("Google Wallet update for card %s failed: HTTP %s", card_number, error.code)
    except Exception:
        logger.exception("Google Wallet update for card %s failed", card_number)
    return False


def update_wallet_class():
    """Bring the pass type's layout up to date at Google (created if new).
    Raises on failure: it is run by the sync_google_wallet command, not on
    a student's request."""
    credentials = _load_credentials()
    if not credentials:
        return False
    body = json.dumps(_pass_class()).encode()
    headers = {'Authorization': f'Bearer {_access_token(credentials)}', 'Content-Type': 'application/json'}
    try:
        request = urllib.request.Request(WALLET_CLASS_API + urllib.parse.quote(_class_id()),
                                         data=body, method='PUT', headers=headers)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    # No class yet: nobody has saved a pass. Create it.
    request = urllib.request.Request(WALLET_CLASS_API.rstrip('/'), data=body, method='POST', headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
        return True


def sync_wallet_pass(student_card):
    """Bring the student's Wallet pass in line with their card."""
    return _patch_pass(student_card.card_number, _pass_details(student_card))


def deactivate_wallet_pass(card_number):
    """Mark the pass for a card number that is no longer in use (reissued
    with a new number, or the account deleted) as inactive."""
    return _patch_pass(card_number, {"state": "INACTIVE"})
