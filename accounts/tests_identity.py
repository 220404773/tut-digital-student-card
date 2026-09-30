"""A proof of registration only verifies the student it belongs to: the
number read from the document must match the number on the account, and the
account email must be that student's own mailbox. Otherwise a classmate's
genuine document, plus your own number or your own inbox, gets you a card.
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import CustomUserCreationForm
from accounts.models import User
from cards.models import OCRMetrics, StudentCard


def current_period():
    """A registration period ending comfortably inside the accepted window,
    worked out from today so the tests don't start failing next year."""
    end = timezone.now().date() + timedelta(days=60)
    start = end - timedelta(days=300)
    return f"{start:%d-%b-%Y} to {end:%d-%b-%Y}"


def ocr_result(student_number, confidence=0.95):
    return {
        'student_number': student_number,
        'full_name': 'Kekeletso Khaka',
        'period': current_period(),
        'confidence_score': confidence,
        'document_classification_confidence': 0.95,
    }


def proof(content=b'%PDF-1.4\n%identity test document'):
    return SimpleUploadedFile('proof.pdf', content, content_type='application/pdf')


def signup_payload(student_number='220404773', email=None, **overrides):
    data = {
        'email': email or f'{student_number}@tut4life.ac.za',
        'first_name': 'Kekeletso',
        'last_name': 'Khaka',
        'student_number': student_number,
        'phone': '',
        'registration_proof': proof(),
        'password1': 'StrongPass123!',
        'password2': 'StrongPass123!',
        'consent': 'on',
    }
    data.update(overrides)
    return data


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class SignupStudentNumberCheckTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_matching_number_verifies_the_account(self):
        with patch('accounts.views.process_registration_proof', return_value=ocr_result('220404773')):
            self.client.post(reverse('signup'), signup_payload('220404773'))

        user = User.objects.get(student_number='220404773')
        self.assertTrue(user.is_verified)
        self.assertNotEqual(user.registration_proof_hash, '')

    def test_formatting_differences_are_not_a_mismatch(self):
        """OCR output like '2204 04773' is the same student as '220404773'."""
        with patch('accounts.views.process_registration_proof', return_value=ocr_result('2204 04773')):
            self.client.post(reverse('signup'), signup_payload('220404773'))

        self.assertTrue(User.objects.get(student_number='220404773').is_verified)

    def test_someone_elses_document_is_not_verified(self):
        """The gap being closed: a classmate's genuine document, your own number."""
        with patch('accounts.views.process_registration_proof', return_value=ocr_result('220404999')):
            response = self.client.post(reverse('signup'), signup_payload('220404773'), follow=True)

        user = User.objects.get(student_number='220404773')
        self.assertFalse(user.is_verified)
        self.assertFalse(StudentCard.objects.filter(user=user).exists())
        self.assertContains(response, 'does not match the one you entered')

    def test_a_mismatched_document_is_not_consumed(self):
        """Not verified means not bound - the real owner can still use their document."""
        with patch('accounts.views.process_registration_proof', return_value=ocr_result('220404999')):
            self.client.post(reverse('signup'), signup_payload('220404773'))

        self.assertEqual(User.objects.get(student_number='220404773').registration_proof_hash, '')

    def test_an_unreadable_number_is_sent_for_review(self):
        with patch('accounts.views.process_registration_proof', return_value=ocr_result(None)):
            response = self.client.post(reverse('signup'), signup_payload('220404773'), follow=True)

        self.assertFalse(User.objects.get(student_number='220404773').is_verified)
        self.assertContains(response, 'could not read a student number')

    def test_a_mismatch_is_flagged_for_manual_review_in_the_metrics(self):
        with patch('accounts.views.process_registration_proof', return_value=ocr_result('220404999')):
            self.client.post(reverse('signup'), signup_payload('220404773'))

        metrics = OCRMetrics.objects.get(user__student_number='220404773')
        self.assertTrue(metrics.student_number_mismatch)
        self.assertTrue(metrics.manual_correction_required)
        self.assertFalse(metrics.is_accurate)


class SignupEmailBindingTests(TestCase):
    @override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
    def test_email_must_be_the_student_mailbox_for_that_number(self):
        """Without this, the victim's number (now matching the document) plus
        your own Gmail passes both checks, and you confirm your own inbox."""
        form = CustomUserCreationForm(
            data={k: v for k, v in signup_payload('220404773', email='attacker@gmail.com').items()
                  if k != 'registration_proof'},
            files={'registration_proof': proof()},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('220404773@tut4life.ac.za', form.errors['email'][0])

    @override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
    def test_student_mailbox_is_accepted_regardless_of_case(self):
        form = CustomUserCreationForm(
            data={k: v for k, v in signup_payload('220404773', email='220404773@TUT4LIFE.ac.za').items()
                  if k != 'registration_proof'},
            files={'registration_proof': proof()},
        )
        self.assertTrue(form.is_valid(), form.errors)

    @override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=False)
    def test_offline_demo_mode_does_not_bind_the_email(self):
        """With confirmation switched off the email isn't used as proof of
        anything, so an offline demo can use any address."""
        form = CustomUserCreationForm(
            data={k: v for k, v in signup_payload('220404773', email='demo@example.com').items()
                  if k != 'registration_proof'},
            files={'registration_proof': proof()},
        )
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class ReuploadIdentityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
            is_verified=True, email_confirmed=True,
        )
        self.card = StudentCard.objects.create(
            user=self.user, card_number='TUT-TEST-0001', status='expired',
            expiry_date=timezone.now().date() - timedelta(days=5),
        )
        self.client.force_login(self.user)

    def _reupload(self, extracted_number):
        with patch('accounts.views.process_registration_proof', return_value=ocr_result(extracted_number)):
            return self.client.post(reverse('reupload_proof'), {'registration_proof': proof()}, follow=True)

    def test_own_current_document_reactivates_the_card(self):
        self._reupload('220404773')
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'active')
        self.assertGreater(self.card.expiry_date, timezone.now().date())

    def test_a_classmates_document_does_not_reactivate_the_card(self):
        response = self._reupload('220404999')
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'expired')
        self.assertContains(response, 'belongs to a different student number')

    def test_a_document_with_no_readable_number_does_not_reactivate_the_card(self):
        self._reupload(None)
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'expired')

    def test_a_revoked_card_cannot_be_reactivated_by_uploading(self):
        """Revocation is an administrator's decision; a document upload must
        not undo it, even with the student's own valid proof."""
        self.card.status = 'revoked'
        self.card.save()

        response = self._reupload('220404773')

        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'revoked')
        self.assertContains(response, 'revoked by an administrator')

    def test_revoked_student_is_turned_away_before_uploading(self):
        self.card.status = 'revoked'
        self.card.save()
        response = self.client.get(reverse('reupload_proof'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_first_card_via_reupload_still_waits_for_email_confirmation(self):
        self.card.delete()
        self.user.email_confirmed = False
        self.user.is_verified = False
        self.user.save()

        self._reupload('220404773')

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertFalse(StudentCard.objects.filter(user=self.user).exists())
