"""IP addresses in the activity data are personal information, so they are
only kept for ACTIVITY_RETENTION_DAYS - after that page visits go, and card
activity keeps the event but loses who it came from."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from cards.models import CardActivity, PageVisit, StudentCard


@override_settings(ACTIVITY_RETENTION_DAYS=90)
class PurgeOldActivityTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )
        card = StudentCard.objects.create(user=user, card_number='TUT-TEST-0001')
        old = timezone.now() - timedelta(days=91)

        self.old_visit = PageVisit.objects.create(path='/old/', ip_address='203.0.113.5')
        self.new_visit = PageVisit.objects.create(path='/new/', ip_address='203.0.113.6')
        self.old_activity = CardActivity.objects.create(
            student_card=card, activity_type='revoked', ip_address='203.0.113.5', user_agent='Firefox',
        )
        self.new_activity = CardActivity.objects.create(
            student_card=card, activity_type='viewed', ip_address='203.0.113.6', user_agent='Chrome',
        )
        # timestamp is auto_now_add, so age the "old" rows after creating them.
        PageVisit.objects.filter(pk=self.old_visit.pk).update(timestamp=old)
        CardActivity.objects.filter(pk=self.old_activity.pk).update(timestamp=old)

    def _run(self, *args):
        call_command('purge_old_activity', *args, stdout=StringIO())

    def test_old_page_visits_are_deleted_and_recent_ones_kept(self):
        self._run()
        self.assertFalse(PageVisit.objects.filter(pk=self.old_visit.pk).exists())
        self.assertTrue(PageVisit.objects.filter(pk=self.new_visit.pk).exists())

    def test_old_card_activity_keeps_the_event_but_loses_the_ip(self):
        self._run()
        self.old_activity.refresh_from_db()
        self.assertEqual(self.old_activity.activity_type, 'revoked')
        self.assertIsNone(self.old_activity.ip_address)
        self.assertIsNone(self.old_activity.user_agent)

    def test_recent_card_activity_is_untouched(self):
        self._run()
        self.new_activity.refresh_from_db()
        self.assertEqual(self.new_activity.ip_address, '203.0.113.6')

    def test_dry_run_changes_nothing(self):
        self._run('--dry-run')
        self.assertTrue(PageVisit.objects.filter(pk=self.old_visit.pk).exists())
        self.old_activity.refresh_from_db()
        self.assertEqual(self.old_activity.ip_address, '203.0.113.5')
