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

    # Once the student has used up their chances to correct the details
    # (tests_signup_rejections.DetailsMustMatchTheDocumentTests).
    @patch('accounts.views.DETAIL_CORRECTION_ATTEMPTS', 0)
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


# Photos aren't what these tests are about - see tests_photo_lock.py.
@override_settings(REQUIRE_PHOTO_FOR_CARD=False)
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

    def test_queue_lists_pending_requests_with_a_decision_form(self):
        response = self.client.get(reverse('report_review_requests'))
        self.assertContains(response, reverse('staff_student_detail', args=[self.student.id]))
        self.assertContains(response, 'Student asked for a review')
        self.assertContains(response, reverse('resolve_review_request', args=[self.review.id]))

    def test_approve_asks_for_confirmation_so_enter_cannot_approve_silently(self):
        """Enter in the date or note field submits through Approve, the first
        button - it must prompt, not verify the student straight away."""
        response = self.client.get(reverse('report_review_requests'))
        self.assertRegex(response.content.decode(), r'value="approve"[^>]*\s+data-confirm="Approve ')

    def test_an_end_date_more_than_a_year_away_is_refused(self):
        self._resolve(action='approve', registration_end_date=(timezone.now().date() + timedelta(days=400)).isoformat())
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'pending')
        self.assertFalse(User.objects.get(pk=self.student.pk).is_verified)

    def test_a_second_decision_on_the_same_review_is_refused_cleanly(self):
        """Two staff approving the same request used to risk a server error."""
        self._resolve(action='approve', registration_end_date=self.end.isoformat())
        response = self.client.post(
            reverse('resolve_review_request', args=[self.review.id]),
            {'action': 'decline'}, follow=True,
        )
        self.assertContains(response, 'already been decided')
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'approved')
        self.assertEqual(StudentCard.objects.filter(user=self.student).count(), 1)

    def test_a_card_failure_undoes_the_whole_decision(self):
        """Nothing half-saved: the student isn't left verified with the
        review still pending."""
        with patch('cards.utils.generate_student_card', side_effect=RuntimeError('storage down')):
            response = self._resolve(action='approve', registration_end_date=self.end.isoformat())
        self.assertEqual(response.status_code, 302)
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'pending')
        self.assertFalse(User.objects.get(pk=self.student.pk).is_verified)

    def test_approving_moves_an_existing_cards_expiry_to_the_confirmed_date(self):
        StudentCard.objects.create(user=self.student, card_number='TUT-OLD-0001',
                                   expiry_date=timezone.now().date() - timedelta(days=3), status='expired')
        self._resolve(action='approve', registration_end_date=self.end.isoformat())
        card = StudentCard.objects.get(user=self.student)
        self.assertEqual((card.expiry_date, card.status), (self.end, 'active'))

    def test_approving_does_not_bring_back_a_revoked_card(self):
        StudentCard.objects.create(user=self.student, card_number='TUT-OLD-0002', status='revoked')
        self._resolve(action='approve', registration_end_date=self.end.isoformat())
        self.assertEqual(StudentCard.objects.get(user=self.student).status, 'revoked')

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

    def test_admin_verified_tick_closes_the_pending_review(self):
        """Verifying a student in the admin used to leave their request
        waiting in the staff queue."""
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        self.student.is_verified = True
        request = RequestFactory().post('/admin/')
        request.user = self.staff
        model_admin = site._registry[User]
        with patch.object(model_admin, 'message_user'):
            model_admin.save_model(request, self.student, form=None, change=True)

        self.review.refresh_from_db()
        self.assertEqual((self.review.status, self.review.resolved_by), ('approved', self.staff))
        self.assertEqual(self.client.get(reverse('report_review_requests')).context['pending'].count(), 0)

    def test_admin_verified_tick_undoes_itself_if_the_card_fails(self):
        """The same all-or-nothing rule as a staff approval: a card failure
        must not leave the student verified with their review closed."""
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        self.student.is_verified = True
        self.student.save()
        request = RequestFactory().post('/admin/')
        request.user = self.staff
        model_admin = site._registry[User]
        with patch.object(model_admin, 'message_user') as message_user, \
                patch('cards.utils.generate_student_card', side_effect=RuntimeError('storage down')):
            model_admin.save_model(request, self.student, form=None, change=True)

        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'pending')
        self.assertFalse(User.objects.get(pk=self.student.pk).is_verified)
        self.assertFalse(StudentCard.objects.filter(user=self.student).exists())
        self.assertIn('left unverified', message_user.call_args.args[1])


class OnePendingReviewTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )

    def test_the_database_refuses_a_second_pending_review(self):
        from django.db import IntegrityError, transaction

        ReviewRequest.objects.create(user=self.student, reason='student_request')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReviewRequest.objects.create(user=self.student, reason='low_confidence')

    def test_open_for_returns_the_existing_request(self):
        first = ReviewRequest.open_for(self.student, 'student_request')
        self.assertEqual(ReviewRequest.open_for(self.student, 'low_confidence'), first)

    def test_a_decided_review_does_not_block_a_new_one(self):
        ReviewRequest.objects.create(user=self.student, reason='student_request', status='declined')
        ReviewRequest.open_for(self.student, 'student_request')
        self.assertEqual(ReviewRequest.objects.filter(user=self.student).count(), 2)


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class SuspiciousDocumentTests(TestCase):
    """A document whose text appeared to address the AI was never shown to
    the model, so the automated check can't verify it - a person does."""

    def setUp(self):
        cache.clear()

    def test_signup_sends_it_to_a_person(self):
        with patch('accounts.views.process_registration_proof',
                   return_value=dict(ocr_result(), needs_human_review=True)):
            self.client.post(reverse('signup'), signup_payload())
        user = User.objects.get()
        self.assertFalse(user.is_verified)
        self.assertEqual(ReviewRequest.objects.get().reason, 'suspicious_content')

    def test_reupload_sends_it_to_a_person(self):
        student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )
        self.client.force_login(student)
        with patch('accounts.views.process_registration_proof',
                   return_value=dict(ocr_result(), needs_human_review=True)):
            self.client.post(reverse('reupload_proof'), {
                'registration_proof': SimpleUploadedFile('p.pdf', b'%PDF-1.4\n%x', content_type='application/pdf'),
            })
        student.refresh_from_db()
        self.assertFalse(student.is_verified)
        self.assertEqual(ReviewRequest.objects.get().reason, 'suspicious_content')


# Photos aren't what these tests are about - see tests_photo_lock.py.
@override_settings(REQUIRE_PHOTO_FOR_CARD=False)
@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class OneCardRuleTests(TestCase):
    """Every path issues the first card through issue_card_if_allowed."""

    def setUp(self):
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', is_verified=True, email_confirmed=True,
        )

    def test_issuing_twice_gives_one_card(self):
        from cards.utils import issue_card_if_allowed
        self.assertEqual(issue_card_if_allowed(self.student), 'issued')
        self.assertEqual(issue_card_if_allowed(self.student), 'exists')
        self.assertEqual(StudentCard.objects.filter(user=self.student).count(), 1)

    def test_an_unverified_student_never_gets_a_card(self):
        from cards.utils import issue_card_if_allowed
        self.student.is_verified = False
        self.student.save()
        self.assertEqual(issue_card_if_allowed(self.student), 'not_verified')
        self.assertFalse(StudentCard.objects.exists())

    def test_restoring_a_deleted_student_waits_for_email_confirmation(self):
        """Restoring is a new verification: the card follows the same rule."""
        from accounts.models import DeletedStudentLog
        staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        entry = DeletedStudentLog.objects.create(
            student_number='220404999', full_name='Former Student', email='220404999@tut4life.ac.za',
            was_verified=True, had_card=True, registration_end_date=timezone.now().date() + timedelta(days=60),
        )
        self.client.force_login(staff)
        self.client.post(reverse('reissue_deleted_student', args=[entry.pk]))
        restored = User.objects.get(student_number='220404999')
        self.assertTrue(restored.is_verified)
        self.assertFalse(StudentCard.objects.filter(user=restored).exists())


class ReviewMessageRetentionTests(TestCase):
    @override_settings(ACTIVITY_RETENTION_DAYS=90)
    def test_old_decided_reviews_lose_the_message_but_keep_the_decision(self):
        from io import StringIO
        from django.core.management import call_command

        student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )
        old = ReviewRequest.objects.create(
            user=student, reason='student_request', message='My personal circumstances...',
            status='declined', staff_note='Not registered', resolved_at=timezone.now() - timedelta(days=91),
        )
        pending = ReviewRequest.objects.create(user=student, reason='student_request', message='Still waiting')

        call_command('purge_old_activity', stdout=StringIO())

        old.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual((old.message, old.status, old.staff_note), ('', 'declined', 'Not registered'))
        self.assertEqual(pending.message, 'Still waiting')


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
