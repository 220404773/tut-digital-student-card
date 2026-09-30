"""Enforces the activity-data retention period (ACTIVITY_RETENTION_DAYS).

    python manage.py purge_old_activity            # apply
    python manage.py purge_old_activity --dry-run  # only report what would change

Run it daily from the server's scheduler, e.g. cron:
    0 2 * * *  cd /app && python manage.py purge_old_activity

Page visits exist only for site statistics, so old rows are deleted outright.
Card activity is the audit trail of who revoked/reissued a card and when, so
those rows are kept - only the IP address and browser string, which identify
the visitor rather than the event, are removed.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from cards.models import CardActivity, PageVisit


class Command(BaseCommand):
    help = 'Delete old page visits and strip IP addresses from old card activity.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report counts without changing anything.')

    def handle(self, *args, **options):
        days = settings.ACTIVITY_RETENTION_DAYS
        cutoff = timezone.now() - timedelta(days=days)

        old_visits = PageVisit.objects.filter(timestamp__lt=cutoff)
        old_activity = CardActivity.objects.filter(timestamp__lt=cutoff).exclude(
            ip_address__isnull=True, user_agent__isnull=True,
        )

        if options['dry_run']:
            self.stdout.write(
                f'Would delete {old_visits.count()} page visit(s) and anonymise '
                f'{old_activity.count()} card activity row(s) older than {days} days.'
            )
            return

        deleted, _ = old_visits.delete()
        anonymised = old_activity.update(ip_address=None, user_agent=None)
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {deleted} page visit(s) and anonymised {anonymised} card activity row(s) '
            f'older than {days} days.'
        ))
