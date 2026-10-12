"""Rules for changing the photo or name on an issued card.

A new photo is compared with the card photo when the student consents
(settings.FACE_COMPARISON_ENABLED). If automatic approval is switched on and
the photos clearly match, it goes on the card straight away; otherwise it
waits for a staff member, with the comparison shown as advice. Nothing is ever
rejected automatically (POPIA s71) - only a person turns a change down.

Every decision is recorded (ReviewRequest.record_card_change) and the student
is told the outcome (accounts/notices.py).
"""
import logging

from django.conf import settings

from .face_checks import compare_faces
from .notices import notify_student

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


def notify_decision(review, request):
    """Tell the student how a person decided their photo or name change."""
    level, text = decision_message(review)
    notify_student(review.user, text, level=level, request=request)
