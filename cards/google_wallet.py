"""
Builds the "Add to Google Wallet" save link for a student's digital card.

Uses the JWT-with-embedded-objects pattern: the class and object definitions
are embedded directly in a signed JWT rather than pre-created via a separate
REST API call to Google - simpler, and needs no extra credential type beyond
the service account's own private key (no OAuth token exchange, no
google-auth dependency).

No custom branding (logo/hero image/background color) is set - per Google's
own schema, only the class `id` is actually required; everything else
renders with Wallet's default styling if omitted.
"""
import json
import time

import jwt
from django.conf import settings


def _load_credentials():
    if not settings.GOOGLE_WALLET_CREDENTIALS_JSON or not settings.GOOGLE_WALLET_ISSUER_ID:
        return None
    return json.loads(settings.GOOGLE_WALLET_CREDENTIALS_JSON)


def build_google_wallet_save_url(student_card, verification_url):
    """Returns a https://pay.google.com/gp/v/save/<jwt> URL the student can
    click to add their card to Google Wallet, or None if Google Wallet
    credentials aren't configured."""
    credentials = _load_credentials()
    if not credentials:
        return None

    issuer_id = settings.GOOGLE_WALLET_ISSUER_ID
    user = student_card.user
    class_id = f"{issuer_id}.tut_digital_student_card"
    object_id = f"{issuer_id}.{student_card.card_number}"

    generic_class = {"id": class_id}

    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "ACTIVE",
        "cardTitle": {
            "defaultValue": {"language": "en-US", "value": "TUT Digital Student Card"}
        },
        "header": {
            "defaultValue": {"language": "en-US", "value": user.get_full_name_display()}
        },
        "textModulesData": [
            {"header": "Student Number", "body": user.student_number, "id": "student_number"},
            {"header": "Card Number", "body": student_card.card_number, "id": "card_number"},
            {
                "header": "Valid Until",
                "body": student_card.expiry_date.strftime('%Y-%m-%d') if student_card.expiry_date else "-",
                "id": "valid_until",
            },
        ],
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
