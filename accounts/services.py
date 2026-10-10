"""Account helpers shared with the cards app.

These used to live in accounts/views.py, so cards/views.py and the
evaluate_ocr command imported another app's views module. Keeping them here,
with no imports from the cards app, means views only ever depend on models
and services, never on each other.
"""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .tokens import email_confirmation_token

logger = logging.getLogger(__name__)


def normalise_student_number(value):
    """Compare student numbers on their characters only - OCR and typing
    both introduce stray spaces, dashes and case differences that don't
    change which student the number identifies."""
    return re.sub(r'[^0-9A-Za-z]', '', value or '').upper()


def split_full_name(full_name):
    """Split an extracted full name into first and last name parts."""
    if not full_name:
        return '', ''

    parts = full_name.split()
    if len(parts) == 1:
        return parts[0].title(), ''

    return parts[0].title(), ' '.join(parts[1:]).title()


# Backends that don't deliver mail anywhere - used to avoid telling a student
# "check your inbox" when nothing was actually sent.
_NON_DELIVERING_EMAIL_BACKENDS = (
    'django.core.mail.backends.console.EmailBackend',
    'django.core.mail.backends.dummy.EmailBackend',
)

EMAIL_NOT_CONFIGURED_MESSAGE = (
    "Email delivery isn't set up on this server, so no confirmation email was sent. "
    "The confirmation link was written to the server log instead."
)


def email_is_delivered():
    return settings.EMAIL_BACKEND not in _NON_DELIVERING_EMAIL_BACKENDS


# Minimum wait between confirmation emails to the same account - matches the
# ~60s resend cooldown most mail-sending flows use (GitHub, Google account
# recovery, etc.), long enough to cover normal delivery delay so a student
# can't spam themselves (and the mail provider) with repeat sends.
EMAIL_CONFIRMATION_COOLDOWN_SECONDS = 60


def _confirmation_cooldown_key(user):
    return f'email_confirmation_cooldown_{user.pk}'


def start_confirmation_cooldown(user):
    allowed_at = timezone.now() + timedelta(seconds=EMAIL_CONFIRMATION_COOLDOWN_SECONDS)
    cache.set(_confirmation_cooldown_key(user), allowed_at, EMAIL_CONFIRMATION_COOLDOWN_SECONDS)


def confirmation_cooldown_remaining(user):
    """Whole seconds left before another confirmation email can be sent to
    this user, or 0 if they're clear to send/resend now."""
    allowed_at = cache.get(_confirmation_cooldown_key(user))
    if not allowed_at:
        return 0
    return max(0, round((allowed_at - timezone.now()).total_seconds()))


def send_confirmation_email(user, request):
    """Email a link that proves the student controls the email address on
    their account - independent of the OCR document check, which only
    confirms a document was read successfully, not who's holding it."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_confirmation_token.make_token(user)
    confirm_path = reverse('confirm_email', args=[uid, token])
    confirm_url = request.build_absolute_uri(confirm_path)

    body = render_to_string('accounts/email_confirmation_email.html', {
        'user': user,
        'confirm_url': confirm_url,
    })
    send_mail(
        subject='Confirm your TUT Digital Student Card email',
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
    start_confirmation_cooldown(user)


def delete_registration_proof(user):
    """Delete the uploaded proof of registration after processing (POPIA
    compliance) - goes through the storage backend's own API (not raw
    os.path/MEDIA_ROOT) so this also works when storage is S3/Spaces-backed,
    not just local disk. Must be called from every code path that saves a
    user with a registration_proof attached, not just the final one - a
    save() partway through signup() already uploads the file, so an early
    return without this call leaves it sitting in storage indefinitely.
    """
    if user.registration_proof:
        try:
            if user.registration_proof.storage.exists(user.registration_proof.name):
                user.registration_proof.storage.delete(user.registration_proof.name)
            user.registration_proof = None
            user.save()
        except Exception as e:
            logger.warning("Error deleting proof file for user %s: %s", user.pk, e)
