"""The card's photo and name are what a guard checks the person showing it
against. So a card is only issued once there is a photo, and after that a new
photo or a new name waits for a staff member - otherwise anyone could put a
friend's face or name on their card.
"""
import io
import shutil
import tempfile

from django.contrib.admin.sites import site
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import ReviewRequest, User
from cards.models import StudentCard
from cards.utils import issue_card_if_allowed

MEDIA = tempfile.mkdtemp()


def photo(colour='red', name='me.png'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), colour).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def profile_form(**extra):
    data = {'first_name': 'Kekeletso', 'last_name': 'Khaka', 'phone': '', 'gender': ''}
    data.update(extra)
    return data


# The test images are plain colour squares, so face detection is off here -
# see tests_face_checks.py.
@override_settings(MEDIA_ROOT=MEDIA, REQUIRE_PHOTO_FOR_CARD=True, REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True,
                   PHOTO_FACE_CHECK_ENABLED=False)
class PhotoLockTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

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

    def _upload_photo(self, colour='red'):
        self.client.force_login(self.student)
        return self.client.post(reverse('profile'), profile_form(profile_picture=photo(colour)), follow=True)

    # -- no card without a photo -------------------------------------------

    def test_no_card_until_there_is_a_photo(self):
        self.assertEqual(issue_card_if_allowed(self.student), 'awaiting_photo')
        self.assertFalse(StudentCard.objects.exists())

    def test_dashboard_asks_for_a_photo(self):
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse('dashboard')), 'Add your photo')

    def test_adding_the_first_photo_issues_the_card(self):
        response = self._upload_photo()
        self.assertTrue(StudentCard.objects.filter(user=self.student).exists())
        self.assertContains(response, 'Your digital student card has been generated')

    def test_confirming_email_without_a_photo_says_what_is_missing(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        from accounts.tokens import email_confirmation_token

        self.student.email_confirmed = False
        self.student.save()
        link = reverse('confirm_email', args=[
            urlsafe_base64_encode(force_bytes(self.student.pk)), email_confirmation_token.make_token(self.student),
        ])
        self.client.force_login(self.student)
        response = self.client.post(link, follow=True)

        self.assertContains(response, 'Add a profile photo on your Profile page')
        self.assertFalse(StudentCard.objects.exists())

    # -- after the card exists, a new photo waits for staff ----------------

    def _student_with_card(self):
        self._upload_photo('red')
        self.student.refresh_from_db()
        return self.student.profile_picture.name

    def test_a_new_photo_does_not_replace_the_card_photo(self):
        original = self._student_with_card()
        response = self._upload_photo('blue')
        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, original)
        self.assertTrue(self.student.pending_profile_picture)
        self.assertContains(response, 'once a staff member approves it')

    def test_dashboard_says_the_new_photo_is_waiting(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.assertContains(self.client.get(reverse('dashboard')), 'New photo waiting for approval')

    def test_a_second_new_photo_replaces_the_first_waiting_one(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.student.refresh_from_db()
        first_pending = self.student.pending_profile_picture.name
        self._upload_photo('green')
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.pending_profile_picture.name, first_pending)

    def test_staff_see_the_change_and_approving_puts_it_on_the_card(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.student.refresh_from_db()
        pending = self.student.pending_profile_picture.name

        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('report_review_requests')), 'New photo uploaded by')
        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'approve'})

        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, pending)
        self.assertFalse(self.student.pending_profile_picture)

    def test_rejecting_keeps_the_card_photo(self):
        original = self._student_with_card()
        self._upload_photo('blue')

        self.client.force_login(self.staff)
        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'reject'})

        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, original)
        self.assertFalse(self.student.pending_profile_picture)

    def test_a_student_cannot_approve_their_own_photo(self):
        original = self._student_with_card()
        self._upload_photo('blue')
        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'approve'})
        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, original)

    def test_the_clear_option_cannot_remove_the_card_photo(self):
        """The profile form's file field has a "clear" checkbox; posting it
        must not take the photo off an issued card."""
        original = self._student_with_card()
        self.client.post(reverse('profile'), profile_form(**{'profile_picture-clear': 'on'}))
        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, original)

    def test_a_replaced_waiting_photo_is_deleted_once_the_new_one_is_saved(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.student.refresh_from_db()
        first_pending = self.student.pending_profile_picture.name
        with self.captureOnCommitCallbacks(execute=True):
            self._upload_photo('green')
        self.assertFalse(default_storage.exists(first_pending))

    def test_approving_deletes_the_old_card_photo_after_saving(self):
        original = self._student_with_card()
        self._upload_photo('blue')
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'approve'})
        self.student.refresh_from_db()
        self.assertFalse(default_storage.exists(original))
        self.assertTrue(default_storage.exists(self.student.profile_picture.name))

    def test_rejecting_deletes_the_waiting_photo_after_saving(self):
        original = self._student_with_card()
        self._upload_photo('blue')
        self.student.refresh_from_db()
        pending = self.student.pending_profile_picture.name
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'reject'})
        self.assertFalse(default_storage.exists(pending))
        self.assertTrue(default_storage.exists(original))

    def test_a_photo_change_decided_twice_is_only_acted_on_once(self):
        """Two staff members on the same page: the second sees it decided."""
        self._student_with_card()
        self._upload_photo('blue')
        self.client.force_login(self.staff)
        url = reverse('resolve_photo_change', args=[self.student.pk])
        self.client.post(url, {'action': 'approve'})
        self.student.refresh_from_db()
        approved = self.student.profile_picture.name
        response = self.client.post(url, {'action': 'reject'}, follow=True)
        self.assertContains(response, 'already been decided')
        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, approved)

    def test_a_photo_decision_is_recorded_under_recent_decisions(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.client.force_login(self.staff)
        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'approve'})
        record = ReviewRequest.objects.get(user=self.student, reason='photo_change')
        self.assertEqual((record.status, record.resolved_by), ('approved', self.staff))
        self.assertIsNotNone(record.resolved_at)
        response = self.client.get(reverse('report_review_requests'))
        self.assertContains(response, 'New card photo')
        self.assertNotContains(response, 'No decisions yet')

    def test_a_rejected_photo_is_recorded_as_declined(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.client.force_login(self.staff)
        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'reject'})
        self.assertEqual(ReviewRequest.objects.get(user=self.student, reason='photo_change').status, 'declined')

    def test_a_recorded_decision_does_not_count_as_waiting(self):
        self._student_with_card()
        self._upload_photo('blue')
        self.client.force_login(self.staff)
        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'approve'})
        stats = self.client.get(reverse('performance_dashboard')).context['card_stats']
        self.assertEqual(stats['pending_total_count'], 0)

    @override_settings(REQUIRE_PHOTO_FOR_CARD=False)
    def test_the_lock_can_be_switched_off_for_an_offline_demo(self):
        self.assertEqual(issue_card_if_allowed(self.student), 'issued')


# The test images are plain colour squares, so face detection is off here -
# see tests_face_checks.py.
@override_settings(MEDIA_ROOT=MEDIA, REQUIRE_PHOTO_FOR_CARD=True, REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True,
                   PHOTO_FACE_CHECK_ENABLED=False)
class NameLockTests(TestCase):
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
        self.client.force_login(self.student)

    def _save_profile(self, **fields):
        return self.client.post(reverse('profile'), profile_form(**fields), follow=True)

    def _give_card(self):
        StudentCard.objects.create(user=self.student, card_number='TUT-NAME-0001')

    def test_before_a_card_exists_the_name_changes_straight_away(self):
        """Correcting a misread name before the card is issued needs no approval."""
        self._save_profile(first_name='Keke')
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Keke')
        self.assertIsNone(self.student.name_change_requested_at)

    def test_after_the_card_is_issued_a_name_change_waits_for_staff(self):
        self._give_card()
        response = self._save_profile(first_name='Someone', last_name='Else')
        self.student.refresh_from_db()
        self.assertEqual((self.student.first_name, self.student.last_name), ('Kekeletso', 'Khaka'))
        self.assertEqual((self.student.pending_first_name, self.student.pending_last_name), ('Someone', 'Else'))
        self.assertContains(response, 'Your new name will appear on your card once a staff member approves it')
        self.assertContains(response, 'Name change waiting for approval')

    def test_the_public_verification_page_keeps_the_card_name_until_approved(self):
        self._give_card()
        self._save_profile(first_name='Someone', last_name='Else')
        self.client.logout()
        response = self.client.get(reverse('verify_card_public', args=['TUT-NAME-0001']))
        self.assertContains(response, 'Kekeletso')
        self.assertNotContains(response, 'Someone')

    def test_other_details_still_save_straight_away(self):
        self._give_card()
        self._save_profile(phone='0821234567')
        self.student.refresh_from_db()
        self.assertEqual(self.student.phone, '0821234567')
        self.assertIsNone(self.student.name_change_requested_at)

    def test_a_new_photo_and_name_together_both_wait(self):
        self._give_card()
        self.student.profile_picture = photo('red', 'old.png')
        self.student.save()
        response = self._save_profile(first_name='Someone', profile_picture=photo('blue'))
        self.assertContains(response, 'Your new photo and name will appear on your card once a staff member approves them')

    def test_staff_approving_puts_the_new_name_on_the_card(self):
        self._give_card()
        self._save_profile(first_name='Keke', last_name='Khaka')
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('report_review_requests')), 'Keke Khaka')
        self.client.post(reverse('resolve_name_change', args=[self.student.pk]), {'action': 'approve'})
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Keke')
        self.assertIsNone(self.student.name_change_requested_at)
        self.assertEqual(self.student.pending_first_name, '')

    def test_staff_rejecting_keeps_the_card_name(self):
        self._give_card()
        self._save_profile(first_name='Someone', last_name='Else')
        self.client.force_login(self.staff)
        self.client.post(reverse('resolve_name_change', args=[self.student.pk]), {'action': 'reject'})
        self.student.refresh_from_db()
        self.assertEqual((self.student.first_name, self.student.last_name), ('Kekeletso', 'Khaka'))
        self.assertIsNone(self.student.name_change_requested_at)

    def test_a_student_cannot_approve_their_own_name_change(self):
        self._give_card()
        self._save_profile(first_name='Someone')
        self.client.post(reverse('resolve_name_change', args=[self.student.pk]), {'action': 'approve'})
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Kekeletso')

    def test_a_name_decision_is_recorded_with_the_old_and_new_name(self):
        self._give_card()
        self._save_profile(first_name='Keke', last_name='Khaka')
        self.client.force_login(self.staff)
        self.client.post(reverse('resolve_name_change', args=[self.student.pk]), {'action': 'approve'})
        record = ReviewRequest.objects.get(user=self.student, reason='name_change')
        self.assertEqual(record.status, 'approved')
        self.assertEqual(record.message, 'Kekeletso Khaka -> Keke Khaka')

    def test_the_profile_page_shows_the_requested_name_while_it_waits(self):
        self._give_card()
        self._save_profile(first_name='Someone', last_name='Else')
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'value="Someone"')
        self.assertContains(response, 'value="Else"')

    def test_saving_another_detail_keeps_the_waiting_name_change(self):
        """The page posts the requested name back, so the request stays -
        with its original date, so it keeps its place in the staff queue."""
        self._give_card()
        self._save_profile(first_name='Someone', last_name='Else')
        self.student.refresh_from_db()
        requested_at = self.student.name_change_requested_at
        self._save_profile(first_name='Someone', last_name='Else', phone='0821234567')
        self.student.refresh_from_db()
        self.assertEqual((self.student.pending_first_name, self.student.pending_last_name), ('Someone', 'Else'))
        self.assertEqual(self.student.name_change_requested_at, requested_at)
        self.assertEqual(self.student.phone, '0821234567')

    def test_going_back_to_the_card_name_withdraws_the_request(self):
        self._give_card()
        self._save_profile(first_name='Someone', last_name='Else')
        response = self._save_profile(first_name='Kekeletso', last_name='Khaka')
        self.student.refresh_from_db()
        self.assertIsNone(self.student.name_change_requested_at)
        self.assertEqual((self.student.pending_first_name, self.student.pending_last_name), ('', ''))
        self.assertContains(response, 'Your name change request was withdrawn')
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse('report_review_requests')), 'Someone Else')

    def test_a_name_change_decided_twice_is_only_acted_on_once(self):
        self._give_card()
        self._save_profile(first_name='Someone', last_name='Else')
        self.client.force_login(self.staff)
        url = reverse('resolve_name_change', args=[self.student.pk])
        self.client.post(url, {'action': 'reject'})
        response = self.client.post(url, {'action': 'approve'}, follow=True)
        self.assertContains(response, 'already been decided')
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Kekeletso')


class ReviewRequestAdminIsViewOnlyTests(TestCase):
    """Editing a request's status in the admin would mark it decided without
    verifying the student or issuing a card."""

    def test_staff_cannot_add_change_or_delete_review_requests_in_the_admin(self):
        model_admin = site._registry[ReviewRequest]
        request = RequestFactory().get('/admin/')
        request.user = User(is_staff=True, is_superuser=True)
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
