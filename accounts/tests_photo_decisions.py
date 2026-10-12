"""Telling the student how a photo or name change was decided, staff reasons
for a rejection, tidy storage before a card exists, and automatic approval
of a new photo that clearly matches the card photo (accounts/photo_changes.py).

The face comparison itself is faked here - see tests_face_checks.py.
"""
import io
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import ReviewRequest, User
from cards.models import StudentCard

MEDIA = tempfile.mkdtemp()


def photo(colour='red'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), colour).save(buf, format='PNG')
    return SimpleUploadedFile('me.png', buf.getvalue(), content_type='image/png')


def profile_form(**extra):
    data = {'first_name': 'Kekeletso', 'last_name': 'Khaka', 'phone': '', 'gender': ''}
    data.update(extra)
    return data


def similarity(score):
    from accounts.face_checks import describe_similarity
    return describe_similarity(score)


@override_settings(MEDIA_ROOT=MEDIA, REQUIRE_PHOTO_FOR_CARD=True, PHOTO_FACE_CHECK_ENABLED=False)
class PhotoDecisionTestCase(TestCase):
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

    def _give_card_with_photo(self):
        self.student.profile_picture = photo('red')
        self.student.save()
        StudentCard.objects.create(user=self.student, card_number='TUT-PHOTO-0001')
        return self.student.profile_picture.name

    def _upload(self, colour='blue', consent=True, score=None):
        """Upload a new photo as the student; score fakes the comparison."""
        self.client.force_login(self.student)
        extra = {'profile_picture': photo(colour)}
        if consent:
            extra['face_compare_consent'] = 'on'
        with patch('accounts.photo_changes.compare_faces',
                   return_value=similarity(score) if score is not None else None) as compare:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('profile'), profile_form(**extra), follow=True)
        self.student.refresh_from_db()
        self.compare = compare
        return response

    def _staff_decides(self, url_name, action, note=''):
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse(url_name, args=[self.student.pk]), {'action': action, 'staff_note': note})
        self.student.refresh_from_db()


class StudentIsToldTheOutcomeTests(PhotoDecisionTestCase):
    def test_a_rejected_photo_is_explained_on_the_dashboard_once(self):
        self._give_card_with_photo()
        self._upload(consent=False)
        self._staff_decides('resolve_photo_change', 'reject', note='Your face is covered by sunglasses.')

        self.client.force_login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Your new photo was not approved')
        self.assertContains(response, 'Reason: Your face is covered by sunglasses.')
        self.assertContains(response, 'app-toast-warning')
        # Next visit: no pop-up, but still listed under the card office's messages.
        again = self.client.get(reverse('dashboard'))
        self.assertNotContains(again, 'app-toast-warning')
        self.assertContains(again, 'Messages from the card office')
        self.assertContains(again, 'Your new photo was not approved')

    def test_the_student_is_emailed_the_decision(self):
        self._give_card_with_photo()
        self._upload(consent=False)
        mail.outbox.clear()
        self._staff_decides('resolve_photo_change', 'reject', note='Please use a photo without a hat.')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['220404773@tut4life.ac.za'])
        self.assertIn('Please use a photo without a hat.', mail.outbox[0].body)

    def test_an_approved_photo_is_announced_on_the_dashboard(self):
        self._give_card_with_photo()
        self._upload(consent=False)
        self._staff_decides('resolve_photo_change', 'approve')
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse('dashboard')), 'Your new photo was approved')

    def test_a_rejected_name_change_gives_the_reason(self):
        StudentCard.objects.create(user=self.student, card_number='TUT-PHOTO-0002')
        self.client.force_login(self.student)
        self.client.post(reverse('profile'), profile_form(first_name='Someone'))
        self._staff_decides('resolve_name_change', 'reject', note="This doesn't match the university's records.")
        self.client.force_login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Your name change was not approved')
        self.assertContains(response, "match the university")

    def test_the_review_page_asks_for_a_reason(self):
        self._give_card_with_photo()
        self._upload(consent=False)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('report_review_requests')), 'Reason, if rejecting')


class PhotoBeforeTheCardTests(PhotoDecisionTestCase):
    def test_replacing_the_photo_before_a_card_deletes_the_old_file(self):
        self.student.is_verified = False  # no card is issued on upload
        self.student.save()
        self._upload('red', consent=False)
        first = self.student.profile_picture.name
        self._upload('blue', consent=False)
        self.assertNotEqual(self.student.profile_picture.name, first)
        self.assertFalse(default_storage.exists(first))
        self.assertTrue(default_storage.exists(self.student.profile_picture.name))

    def test_clearing_the_photo_before_a_card_deletes_the_file(self):
        self.student.is_verified = False
        self.student.save()
        self._upload('red', consent=False)
        first = self.student.profile_picture.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('profile'), profile_form(**{'profile_picture-clear': 'on'}))
        self.assertFalse(default_storage.exists(first))


@override_settings(FACE_COMPARISON_ENABLED=True, FACE_AUTO_APPROVE_ENABLED=True, FACE_AUTO_APPROVE_MIN_SIMILARITY=0.75)
class AutomaticApprovalTests(PhotoDecisionTestCase):
    def test_a_clear_match_goes_on_the_card_straight_away(self):
        original = self._give_card_with_photo()
        response = self._upload(score=0.82)
        self.assertNotEqual(self.student.profile_picture.name, original)
        self.assertFalse(self.student.pending_profile_picture)
        self.assertEqual(self.student.previous_profile_picture.name, original)
        self.assertContains(response, 'matched your card photo (similarity 0.82)')
        record = ReviewRequest.objects.get(user=self.student, reason='photo_change')
        self.assertEqual((record.status, record.resolved_by), ('approved', None))
        self.assertEqual(record.message, 'Automatic (similarity 0.82)')

    def test_exactly_the_threshold_is_approved(self):
        original = self._give_card_with_photo()
        self._upload(score=0.75)
        self.assertNotEqual(self.student.profile_picture.name, original)

    def test_below_the_threshold_waits_for_staff_with_the_score_shown(self):
        original = self._give_card_with_photo()
        self._upload(score=0.74)
        self.assertEqual(self.student.profile_picture.name, original)
        self.assertTrue(self.student.pending_profile_picture)
        self.assertEqual(self.student.pending_photo_similarity, 0.74)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('report_review_requests'))
        self.assertContains(response, 'similarity 0.74')
        self.assertContains(response, 'Below the 0.75 needed for automatic approval')

    def test_a_different_person_is_never_rejected_automatically(self):
        self._give_card_with_photo()
        self._upload(score=0.05)
        self.assertTrue(self.student.pending_profile_picture)
        self.assertFalse(ReviewRequest.objects.filter(user=self.student, status='declined').exists())

    def test_without_consent_nothing_is_compared(self):
        self._give_card_with_photo()
        self._upload(consent=False, score=0.95)
        self.compare.assert_not_called()
        self.assertTrue(self.student.pending_profile_picture)

    @override_settings(FACE_AUTO_APPROVE_ENABLED=False)
    def test_switched_off_it_only_advises(self):
        self._give_card_with_photo()
        self._upload(score=0.95)
        self.assertTrue(self.student.pending_profile_picture)
        self.assertEqual(self.student.pending_photo_similarity, 0.95)

    def test_never_twice_in_a_row(self):
        """Small automatic steps must not be able to swap the face over."""
        self._give_card_with_photo()
        self._upload('blue', score=0.9)
        after_first = self.student.profile_picture.name
        self._upload('green', score=0.9)
        self.assertEqual(self.student.profile_picture.name, after_first)
        self.assertTrue(self.student.pending_profile_picture)

    def test_a_staff_approval_allows_automatic_approval_again(self):
        self._give_card_with_photo()
        self._upload('blue', score=0.9)
        self._upload('green', score=0.9)  # waits, because the last one was automatic
        kept = self.student.previous_profile_picture.name
        self._staff_decides('resolve_photo_change', 'approve')
        self.assertIsNone(self.student.photo_auto_approved_at)
        self.assertFalse(self.student.previous_profile_picture)
        self.assertFalse(default_storage.exists(kept))
        approved = self.student.profile_picture.name
        self._upload('yellow', score=0.9)
        self.assertNotEqual(self.student.profile_picture.name, approved)

    def test_staff_can_undo_an_automatic_approval(self):
        original = self._give_card_with_photo()
        self._upload(score=0.9)
        automatic = self.student.profile_picture.name
        record = ReviewRequest.objects.get(user=self.student, reason='photo_change')

        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('report_review_requests')), 'Undo')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('undo_automatic_photo_approval', args=[record.pk]))
        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_picture.name, original)
        self.assertFalse(default_storage.exists(automatic))
        self.assertTrue(ReviewRequest.objects.filter(user=self.student, status='declined',
                                                     message='Automatic approval undone').exists())

        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse('dashboard')), 'reversed by staff')

    def test_an_automatic_approval_can_only_be_undone_once(self):
        self._give_card_with_photo()
        self._upload(score=0.9)
        record = ReviewRequest.objects.get(user=self.student, reason='photo_change')
        self.client.force_login(self.staff)
        url = reverse('undo_automatic_photo_approval', args=[record.pk])
        self.client.post(url)
        response = self.client.post(url, follow=True)
        self.assertContains(response, 'can no longer be undone')

    def test_a_student_cannot_undo(self):
        original = self._give_card_with_photo()
        self._upload(score=0.9)
        record = ReviewRequest.objects.get(user=self.student, reason='photo_change')
        self.client.post(reverse('undo_automatic_photo_approval', args=[record.pk]))
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.profile_picture.name, original)

    def test_the_kept_photo_is_deleted_after_the_undo_window(self):
        original = self._give_card_with_photo()
        self._upload(score=0.9)
        User.objects.filter(pk=self.student.pk).update(photo_auto_approved_at=timezone.now() - timedelta(days=31))
        call_command('purge_old_activity', stdout=io.StringIO())
        self.student.refresh_from_db()
        self.assertFalse(self.student.previous_profile_picture)
        self.assertFalse(default_storage.exists(original))


@override_settings(FACE_COMPARISON_ENABLED=True, FACE_AUTO_APPROVE_ENABLED=True, FACE_AUTO_APPROVE_MIN_SIMILARITY=0.75)
class PhotoChangePerformanceTests(PhotoDecisionTestCase):
    """The dashboard section that shows whether the threshold is right."""

    def _stats(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('performance_dashboard'))
        self.assertContains(response, 'Photo Change Performance')
        return response.context['photo_stats']

    def test_the_score_is_kept_with_each_decision(self):
        self._give_card_with_photo()
        self._upload(score=0.62)
        self._staff_decides('resolve_photo_change', 'approve')
        self.assertEqual(ReviewRequest.objects.get(user=self.student, reason='photo_change').similarity, 0.62)

    def test_automatic_and_staff_decisions_are_counted(self):
        self._give_card_with_photo()
        self._upload('blue', score=0.9)            # automatic
        self._upload('green', score=0.62)          # waits (never twice in a row)
        self._staff_decides('resolve_photo_change', 'approve')
        stats = self._stats()
        self.assertEqual((stats['total'], stats['automatic'], stats['staff_approved']), (2, 1, 1))
        self.assertEqual((stats['compared'], stats['compared_rate']), (2, 100))

    @override_settings(FACE_COMPARISON_ENABLED=False)
    def test_with_comparison_off_the_tile_says_staff_compare_by_eye(self):
        self._give_card_with_photo()
        self._upload(consent=False)
        self._staff_decides('resolve_photo_change', 'approve')
        self.client.force_login(self.staff)
        response = self.client.get(reverse('performance_dashboard'))
        self.assertContains(response, 'Face comparison is off - staff compare by eye')
        self.assertNotContains(response, 'Is the threshold right?')

    def test_the_reason_box_shows_the_whole_text(self):
        self._give_card_with_photo()
        self._upload(consent=False)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('report_review_requests')), 'field-sizing: content')
