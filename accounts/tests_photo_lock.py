"""The card's photo is what a guard compares with the person showing it, so a
card is only issued once there is a photo, and after that a new photo waits
for a staff member - otherwise anyone could put a friend's face on their card.
"""
import io
import shutil
import tempfile

from django.contrib.admin.sites import site
from django.core.cache import cache
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


@override_settings(MEDIA_ROOT=MEDIA, REQUIRE_PHOTO_FOR_CARD=True, REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
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

    @override_settings(REQUIRE_PHOTO_FOR_CARD=False)
    def test_the_lock_can_be_switched_off_for_an_offline_demo(self):
        self.assertEqual(issue_card_if_allowed(self.student), 'issued')


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
