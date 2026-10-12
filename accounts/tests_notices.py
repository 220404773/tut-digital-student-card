"""Every staff action on a student's account is communicated to them: a
notice that pops up once on their dashboard and stays listed there, and an
email (accounts/notices.py). A deleted account gets the email only.
"""
import io
from datetime import timedelta
from types import SimpleNamespace

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import ReviewRequest, StudentNotice, User
from cards.models import StudentCard


@override_settings(REQUIRE_PHOTO_FOR_CARD=False, REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class StaffActionsAreCommunicatedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', first_name='Kekeletso', last_name='Khaka',
            is_verified=True, email_confirmed=True,
        )
        self.staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        self.client.force_login(self.staff)
        mail.outbox.clear()

    def _as_staff(self, url, data=None):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data or {})

    def _notice(self):
        return StudentNotice.objects.get(user=self.student)

    def _emailed(self, text):
        self.assertTrue(any(text in message.body and message.to == [self.student.email] for message in mail.outbox),
                        f'No email to the student containing {text!r}')

    def _give_card(self, **fields):
        return StudentCard.objects.create(user=self.student, card_number='TUT-NOTICE-0001',
                                          expiry_date=timezone.now().date() + timedelta(days=90), **fields)

    # -- registration reviews ------------------------------------------------

    def test_an_approved_review_is_communicated(self):
        User.objects.filter(pk=self.student.pk).update(is_verified=False)
        review = ReviewRequest.objects.create(user=self.student, reason='student_request')
        end = timezone.now().date() + timedelta(days=60)
        self._as_staff(reverse('resolve_review_request', args=[review.pk]),
                       {'action': 'approve', 'registration_end_date': end.isoformat()})
        notice = self._notice()
        self.assertEqual(notice.level, 'success')
        self.assertIn('Your registration has been verified', notice.text)
        self.assertIn('card is ready', notice.text)
        self._emailed('Your registration has been verified')

    def test_a_declined_review_gives_the_reason(self):
        User.objects.filter(pk=self.student.pk).update(is_verified=False)
        review = ReviewRequest.objects.create(user=self.student, reason='student_request')
        self._as_staff(reverse('resolve_review_request', args=[review.pk]),
                       {'action': 'decline', 'staff_note': 'Not registered for 2026.'})
        self.assertIn('Reason: Not registered for 2026.', self._notice().text)
        self._emailed('Not registered for 2026.')

    # -- cards ---------------------------------------------------------------

    def test_a_revoked_card_is_communicated(self):
        self._give_card()
        self._as_staff(reverse('admin:accounts_user_revoke_card', args=[self.student.pk]))
        self.assertEqual(self._notice().level, 'danger')
        self._emailed('has been revoked')

    def test_a_reissued_card_warns_the_old_qr_code_stops_working(self):
        self._give_card()
        self._as_staff(reverse('admin:accounts_user_reissue_card', args=[self.student.pk]))
        self.assertIn('old QR code', self._notice().text)
        self._emailed('new card number')

    def test_a_first_card_issued_by_staff_is_communicated(self):
        self._as_staff(reverse('admin:accounts_user_reissue_card', args=[self.student.pk]))
        self.assertIn('has been issued', self._notice().text)

    def test_a_card_status_changed_in_the_card_admin_is_communicated(self):
        from django.contrib.admin.sites import site

        card = self._give_card()
        card.status = 'revoked'
        form = SimpleNamespace(changed_data=['status'])
        request = SimpleNamespace(user=self.staff, build_absolute_uri=lambda path: 'http://testserver' + path)
        with self.captureOnCommitCallbacks(execute=True):
            site._registry[StudentCard].save_model(request, card, form, change=True)
        self.assertIn('has been revoked', self._notice().text)

    # -- the student's details -----------------------------------------------

    def _admin_save(self, changed, **fields):
        from django.contrib.admin.sites import site

        model_admin = site._registry[User]
        for name, value in fields.items():
            setattr(self.student, name, value)
        labels = {'first_name': 'First name', 'email': 'Email address', 'is_verified': 'Is verified',
                  'is_active': 'Active'}
        form = SimpleNamespace(changed_data=changed,
                               fields={name: SimpleNamespace(label=labels.get(name, name)) for name in changed})
        request = self.client.request().wsgi_request
        request.user = self.staff
        with self.captureOnCommitCallbacks(execute=True):
            model_admin._notify_changes(self.student, form, request)

    def test_details_changed_by_staff_are_communicated(self):
        self._admin_save(['first_name', 'email'], first_name='Keke')
        self.assertEqual(self._notice().text, 'The student card office updated your first name, email address.')

    def test_a_deactivated_account_is_communicated(self):
        self._admin_save(['is_active'], is_active=False)
        self.assertIn('deactivated', self._notice().text)
        self._emailed('deactivated')

    def test_a_deleted_account_is_emailed(self):
        self._as_staff(reverse('admin:accounts_user_delete', args=[self.student.pk]), {'post': 'yes'})
        self.assertFalse(User.objects.filter(pk=self.student.pk).exists())
        self._emailed('has been closed')

    def test_staff_accounts_are_never_notified(self):
        from accounts.notices import notify_student

        self.assertIsNone(notify_student(self.staff, 'Anything'))
        self.assertFalse(StudentNotice.objects.exists())

    # -- what the student sees -------------------------------------------------

    def test_the_dashboard_pops_it_up_once_and_keeps_it_listed(self):
        self._give_card()
        self._as_staff(reverse('admin:accounts_user_reissue_card', args=[self.student.pk]))
        self.client.force_login(self.student)
        first = self.client.get(reverse('dashboard'))
        self.assertContains(first, 'app-toast-warning')
        again = self.client.get(reverse('dashboard'))
        self.assertNotContains(again, 'app-toast-warning')
        self.assertContains(again, 'Messages from the card office')
        self.assertContains(again, 'reissued with a new card number')

    @override_settings(ACTIVITY_RETENTION_DAYS=30)
    def test_old_notices_are_deleted_by_the_retention_clean_up(self):
        from accounts.notices import notify_student

        notice = notify_student(self.student, 'Old news', email=False)
        StudentNotice.objects.filter(pk=notice.pk).update(created_at=timezone.now() - timedelta(days=60))
        call_command('purge_old_activity', stdout=io.StringIO())
        self.assertFalse(StudentNotice.objects.exists())
