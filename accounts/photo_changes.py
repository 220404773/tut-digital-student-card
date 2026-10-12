"""Rules for changing the photo or name on an issued card.

A new photo is compared with the card photo when the student consents
(settings.FACE_COMPARISON_ENABLED). If automatic approval is switched on and
the photos clearly match, it goes on the card straight away; otherwise it
waits for a staff member, with the comparison shown as advice. Nothing is ever
rejected automatically (POPIA s71) - only a person turns a change down.

Every decision is recorded (ReviewRequest.record_card_change) and the student
is told the outcome: on their dashboard, and by email when a person decided.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .face_checks import compare_faces

logger = logging.getLogger(__name__)


def compare_with_card_photo(card_photo_name, storage, new_file):
    """Advice on whether new_file shows the same person as the card photo,
    or None if it couldn't be worked out. Only the result is kept."""
    if not card_photo_name:
        return None
    try:
        with storage.open(card_photo_name, 'rb') as card_photo:
            return compare_faces(card_photo, new_file)
    except OSError:
        logger.warning("Could not open the card photo for comparison")
        return None


def can_approve_automatically(user, comparison):
    """True if a new photo may go on the card without a person checking it."""
    return bool(
        settings.FACE_COMPARISON_ENABLED
        and settings.FACE_AUTO_APPROVE_ENABLED
        and comparison
        and comparison['score'] >= settings.FACE_AUTO_APPROVE_MIN_SIMILARITY
        # Never twice in a row: each automatic step is compared with the
        # photo before it, so a chain of them could drift to another face.
        and user.photo_auto_approved_at is None
    )


def decision_message(review):
    """(level, text) telling the student how their change was decided."""
    approved = review.status == 'approved'
    if review.reason == 'photo_change':
        if approved:
            text = 'Your new photo was approved and is now on your card.'
        else:
            text = 'Your new photo was not approved, so your card keeps its current photo.'
    else:
        if approved:
            text = 'Your name change was approved and your card now shows your new name.'
        else:
            text = (f'Your name change was not approved, so your card keeps the name '
                    f'{review.user.get_full_name_display()}.')
    if review.staff_note and not approved:
        text += f' Reason: {review.staff_note}'
    return ('success' if approved else 'warning'), text


def email_decision(review, request):
    """Email the student a decision a staff member made. A failure is logged,
    not raised: the dashboard tells them anyway."""
    _, text = decision_message(review)
    what = 'photo' if review.reason == 'photo_change' else 'name change'
    try:
        send_mail(
            subject=f'Your TUT Digital Student Card {what}',
            message=(
                f"Hello {review.user.first_name or 'student'},\n\n{text}\n\n"
                f"Your card: {request.build_absolute_uri(reverse('dashboard'))}\n\n"
                "TUT Digital Student Card"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[review.user.email],
        )
    except Exception:
        logger.exception("Could not email card-change decision %s", review.pk)
