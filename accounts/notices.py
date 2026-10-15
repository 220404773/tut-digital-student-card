"""Telling a student about something a staff member did to their account.

notify_student() records a StudentNotice and emails it. The student sees it
once as a pop-up the next time they open their dashboard
(show_new_notices), and it stays listed there afterwards - so a decision
isn't lost if they miss the pop-up or the email.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import StudentNotice

logger = logging.getLogger(__name__)

# Wording shared by the places that can do the same thing to a card.
REVOKED_NOTICE = (
    'Your digital student card has been revoked by the student card office, so it shows as invalid when '
    'scanned. Contact the student card office if you think this is a mistake.'
)
REISSUED_NOTICE = (
    'Your digital student card has been reissued with a new card number. Your old QR code, and any saved or '
    'printed copy of it, no longer works - use the card on your Home page, and download the QR code again if '
    'you need it. If you added your card to Google Wallet, add the new one too.'
)
CLOSED_NOTICE = (
    'Your TUT Digital Student Card account has been closed by the student card office, and your digital card '
    'no longer works. Contact the student card office if you think this is a mistake.'
)

_MESSAGE_LEVELS = {
    'success': messages.SUCCESS,
    'info': messages.INFO,
    'warning': messages.WARNING,
    'danger': messages.ERROR,
}


def notify_student(student, text, level='info', request=None, email=True, already_shown=False):
    """Record a notice for the student and, once the current change has
    been saved, email it. already_shown: the student is looking at the
    outcome right now (it popped up on the page they're on), so it is only
    listed with their messages, not popped up again or emailed.
    Staff accounts are never notified."""
    if student.is_staff:
        return None
    notice = StudentNotice.objects.create(
        user=student, level=level, text=text, shown_at=timezone.now() if already_shown else None,
    )
    if email:
        transaction.on_commit(lambda: email_student(student, text, request))
    return notice


def email_student(student, text, request=None):
    """Email a notice. A failure is logged, not raised: the dashboard shows
    the notice anyway, and staff shouldn't see their action fail over it."""
    if request is not None:
        link = request.build_absolute_uri(reverse('dashboard'))
    elif settings.SITE_URL:
        link = settings.SITE_URL.rstrip('/') + reverse('dashboard')
    else:
        link = ''
    try:
        send_mail(
            subject='An update on your TUT Digital Student Card',
            message=(
                f"Hello {student.first_name or 'student'},\n\n{text}\n\n"
                + (f"Your card: {link}\n\n" if link else '')
                + "TUT Digital Student Card"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
        )
    except Exception:
        logger.exception("Could not email a notice to student %s", student.pk)


def show_new_notices(request, student):
    """Pop up the notices the student hasn't seen yet, oldest first."""
    new = list(StudentNotice.objects.filter(user=student, shown_at__isnull=True).order_by('created_at'))
    for notice in new:
        messages.add_message(request, _MESSAGE_LEVELS.get(notice.level, messages.INFO), notice.text)
    if new:
        StudentNotice.objects.filter(pk__in=[notice.pk for notice in new]).update(shown_at=timezone.now())
