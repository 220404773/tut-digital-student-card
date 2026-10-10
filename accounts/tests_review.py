"""POPIA s71: the automated document check is never the final word. Cases
the system is unsure of go to a staff member automatically, and a student
can ask for a person to decide when the check rejects them."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import ReviewRequest, User
from cards.models import StudentCard


def current_period():
    end = timezone.now().date() + timedelta(days=60)
    return f"{end - timedelta(days=300):%d-%b-%Y} to {end:%d-%b-%Y}"


def ocr_result(student_number='220404773', confidence=0.95):
    return {
        'student_number': student_number, 'full_name': 'Kekeletso Khaka', 'period': current_period(),
        'confidence_score': confidence, 'document_classification_confidence': 0.95,
    }


def signup_payload(**overrides):
    data = {
        'email': '220404773@tut4life.ac.za', 'first_name': 'Kekeletso', 'last_name': 'Khaka',
        'student_number': '220404773', 'phone': '',
        'registration_proof': SimpleUploadedFile('proof.pdf', b'%PDF-1.4\n%review test', content_type='application/pdf'),
        'password1': 'StrongPass123!', 'password2': 'StrongPass123!', 'consent': 'on',
    }
    data.update(overrides)
    return data


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class AutomaticReviewTests(TestCase):
    """Where the system itself is unsure, a person decides - the student
    doesn't have to ask."""

    def setUp(self):
        cache.clear()

    def _signup(self, result, **overrides):
        with patch('accounts.views.process_registration_proof', return_value=result):
            return self.client.post(reverse('signup'), signup_payload(**overrides), follow=True)

    def test_student_number_mismatch_opens_a_review(self):
        self._signup(ocr_result(student_number='220404999'))
        review = ReviewRequest.objects.get(user__student_number='220404773')
        self.assertEqual((review.reason, review.status), ('number_mismatch', 'pending'))

    def test_borderline_confidence_opens_a_review(self):
        self._signup(ocr_result(confidence=0.77))
        self.assertEqual(ReviewRequest.objects.get().reason, 'low_confidence')

    def test_a_clear_failure_does_not_open_one_automatically(self):
        self._signup(ocr_result(confidence=0.4))
        self.assertFalse(ReviewRequest.objects.exists())

    def test_a_verified_signup_opens_no_review(self):
        self._signup(ocr_result())
        self.assertTrue(User.objects.get().is_verified)
        self.assertFalse(ReviewRequest.objects.exists())


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class RejectedDocumentReviewTests(TestCase):
    def setUp(self):
        cache.clear()

    def _signup(self, **overrides):
        with patch('accounts.views.process_registration_proof', return_value={'error': 'invalid_pdf'}):
            return self.client.post(reverse('signup'), signup_payload(**overrides))

    def test_by_default_a_rejected_document_creates_no_account(self):
        """FR-03 still holds - unless the student asks for a person."""
        response = self._signup()
        self.assertFalse(User.objects.exists())
        self.assertContains(response, 'Ask a staff member to verify my registration')

    def test_asking_for_a_person_creates_an_unverified_account_and_a_review(self):
        self._signup(request_review='on')
        user = User.objects.get()
        self.assertFalse(user.is_verified)
        self.assertFalse(StudentCard.objects.exists())
        self.assertEqual(ReviewRequest.objects.get().reason, 'document_rejected')


class StudentRequestTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )
        self.client.force_login(self.student)

    def test_unverified_student_sees_the_options_not_a_false_processing_message(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Ask a staff member to review')
        self.assertNotContains(response, 'being processed')

    def test_asking_creates_one_pending_request_with_the_message(self):
        self.client.post(reverse('request_review'), {'message': 'My proof was a scan.'})
        self.client.post(reverse('request_review'), {'message': 'Again'})
        review = ReviewRequest.objects.get()
        self.assertEqual((review.reason, review.message), ('student_request', 'My proof was a scan.'))

    def test_dashboard_shows_the_request_is_under_review(self):
        self.client.post(reverse('request_review'))
        self.assertContains(self.client.get(reverse('dashboard')), 'Under review')

    def test_a_verified_student_cannot_queue_a_request(self):
        self.student.is_verified = True
        self.student.save()
        self.client.post(reverse('request_review'))
        self.assertFalse(ReviewRequest.objects.exists())


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class StaffDecisionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', email_confirmed=True,
        )
        self.review = ReviewRequest.open_for(self.student, 'student_request')
        self.end = timezone.now().date() + timedelta(days=90)
        self.client.force_login(self.staff)

    def _resolve(self, **data):
        return self.client.post(reverse('resolve_review_request', args=[self.review.id]), data)

    def test_queue_lists_pending_requests(self):
        self.assertContains(self.client.get(reverse('report_review_requests')), '220404773')

    def test_approving_verifies_the_student_and_issues_the_card(self):
        self._resolve(action='approve', registration_end_date=self.end.isoformat())
        self.student.refresh_from_db()
        self.review.refresh_from_db()
        self.assertTrue(self.student.is_verified)
        self.assertEqual(self.student.registration_end_date, self.end)
        self.assertEqual(StudentCard.objects.get(user=self.student).expiry_date, self.end)
        self.assertEqual((self.review.status, self.review.resolved_by), ('approved', self.staff))

    def test_approval_still_waits_for_email_confirmation(self):
        self.student.email_confirmed = False
        self.student.save()
        self._resolve(action='approve', registration_end_date=self.end.isoformat())
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_verified)
        self.assertFalse(StudentCard.objects.filter(user=self.student).exists())

    def test_approving_needs_a_confirmed_end_date(self):
        self._resolve(action='approve')
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'pending')
        self.assertFalse(User.objects.get(pk=self.student.pk).is_verified)

    def test_an_end_date_already_past_is_refused(self):
        self._resolve(action='approve', registration_end_date=(timezone.now().date() - timedelta(days=1)).isoformat())
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'pending')

    def test_declining_shows_the_note_on_the_students_dashboard(self):
        self._resolve(action='decline', staff_note='Not registered for 2026 per iEnabler.')
        self.client.force_login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Review declined')
        self.assertContains(response, 'Not registered for 2026 per iEnabler.')

    def test_students_cannot_resolve_reviews(self):
        self.client.force_login(self.student)
        self._resolve(action='approve', registration_end_date=self.end.isoformat())
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'pending')

    def test_admin_verified_tick_also_waits_for_email_confirmation(self):
        """The admin path used to issue a card immediately, skipping the step
        every other path requires."""
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        self.student.email_confirmed = False
        self.student.is_verified = True
        model_admin = site._registry[User]
        request = RequestFactory().post('/admin/')
        request.user = self.staff
        with patch.object(model_admin, 'message_user') as message_user:
            model_admin.save_model(request, self.student, form=None, change=True)

        self.assertFalse(StudentCard.objects.filter(user=self.student).exists())
        self.assertIn('once they confirm their email', message_user.call_args.args[1])


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class ReuploadClosesReviewTests(TestCase):
    def test_verifying_with_own_document_closes_the_open_review(self):
        cache.clear()
        student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )
        review = ReviewRequest.open_for(student, 'student_request')
        self.client.force_login(student)
        with patch('accounts.views.process_registration_proof', return_value=ocr_result()):
            self.client.post(reverse('reupload_proof'), {
                'registration_proof': SimpleUploadedFile('p.pdf', b'%PDF-1.4\n%x', content_type='application/pdf'),
            })
        review.refresh_from_db()
        self.assertEqual(review.status, 'approved')
        self.assertIsNone(review.resolved_by)
