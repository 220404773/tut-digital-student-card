"""Enforces the activity-data retention period (ACTIVITY_RETENTION_DAYS).

    python manage.py purge_old_activity            # apply
    python manage.py purge_old_activity --dry-run  # only report what would change

The app runs it once a day by itself (cards/middleware.py); it can also be
run by hand or from a scheduler, e.g. cron:
    0 2 * * *  cd /app && python manage.py purge_old_activity

Page visits exist only for site statistics, so old rows are deleted outright.
Card activity is the audit trail of who revoked/reissued a card and when, so
those rows are kept - only the IP address and browser string, which identify
the visitor rather than the event, are removed.

Messages from the card office to a student (StudentNotice) are deleted once
older than the retention period too.

A photo replaced by an automatic approval is kept for
AUTO_APPROVED_PHOTO_UNDO_DAYS so staff can undo it, then deleted.

Review requests that were decided before the cutoff lose the student's
free-text message; the decision itself (reason, outcome, who decided, when
and the staff note) is kept as the record that a person made it (POPIA s71).
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts.models import ReviewRequest, StudentNotice, User
from cards.models import CardActivity, PageVisit


class Command(BaseCommand):
    help = 'Delete old page visits, strip IP addresses from old card activity and clear old review messages.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report counts without changing anything.')

    def handle(self, *args, **options):
        days = settings.ACTIVITY_RETENTION_DAYS
        cutoff = timezone.now() - timedelta(days=days)

        old_visits = PageVisit.objects.filter(timestamp__lt=cutoff)
        old_activity = CardActivity.objects.filter(timestamp__lt=cutoff).exclude(
            ip_address__isnull=True, user_agent__isnull=True,
        )
        old_review_messages = ReviewRequest.objects.exclude(status='pending').filter(
            resolved_at__lt=cutoff,
        ).exclude(message='')
        undo_cutoff = timezone.now() - timedelta(days=settings.AUTO_APPROVED_PHOTO_UNDO_DAYS)
        kept_photo = Q(previous_profile_picture__gt='') | Q(photo_before_automatic__gt='')
        expired_undo_photos = User.objects.filter(kept_photo, photo_auto_approved_at__lt=undo_cutoff)

        if options['dry_run']:
            self.stdout.write(
                f'Would delete {old_visits.count()} page visit(s) and anonymise '
                f'{old_activity.count()} card activity row(s), and clear {old_review_messages.count()} '
                f'review message(s), older than {days} days.'
            )
            return

        deleted, _ = old_visits.delete()
        anonymised = old_activity.update(ip_address=None, user_agent=None)
        cleared = old_review_messages.update(message='')
        StudentNotice.objects.filter(created_at__lt=cutoff).delete()
        for student in expired_undo_photos:
            # After the undo window neither the kept photo nor the run's
            # starting photo is kept; the count stays, and without its
            # starting photo a run can't continue - a person checks next.
            current = student.profile_picture.name
            for kept in {student.previous_profile_picture.name, student.photo_before_automatic.name}:
                if kept and kept != current:
                    student.profile_picture.storage.delete(kept)
            student.previous_profile_picture = None
            student.photo_before_automatic = None
            student.save(update_fields=['previous_profile_picture', 'photo_before_automatic'])
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {deleted} page visit(s), anonymised {anonymised} card activity row(s) and cleared '
            f'{cleared} review message(s) older than {days} days.'
        ))
