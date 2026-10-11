"""A document rejected at signup creates no account, so the student can fix
it and submit again. Saving one used to leave them with an account they
weren't logged in to, and the retry said the email was already registered.
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.tests_identity import ocr_result, signup_payload


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class RejectedDocumentCreatesNoAccountTests(TestCase):
    def setUp(self):
        cache.clear()

    def _signup(self, result):
        with patch('accounts.views.process_registration_proof', return_value=result):
            return self.client.post(reverse('signup'), signup_payload())

    def test_a_document_without_details_creates_no_account(self):
        result = ocr_result(None)
        result['full_name'] = None
        response = self._signup(result)
        self.assertContains(response, 'did not contain the required details')
        self.assertFalse(User.objects.filter(student_number='220404773').exists())

    def test_an_expired_registration_creates_no_account(self):
        result = ocr_result('220404773')
        result['period'] = '01-Jan-2020 to 31-Dec-2020'
        response = self._signup(result)
        self.assertContains(response, 'expired on')
        self.assertFalse(User.objects.filter(student_number='220404773').exists())

    def test_an_implausible_end_date_creates_no_account(self):
        result = ocr_result('220404773')
        far = timezone.now().date() + timedelta(days=365 * 5)
        result['period'] = f'01-Jan-2026 to {far:%d-%b-%Y}'
        response = self._signup(result)
        self.assertContains(response, 'look right')
        self.assertFalse(User.objects.filter(student_number='220404773').exists())

    def test_after_a_rejection_the_student_can_sign_up_with_a_good_document(self):
        result = ocr_result('220404773')
        result['period'] = '01-Jan-2020 to 31-Dec-2020'
        self._signup(result)
        response = self._signup(ocr_result('220404773'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(student_number='220404773').is_verified)

    def test_a_failure_before_the_document_is_read_does_not_crash(self):
        """The second hash (checking the preview cache) fails: the account is
        still created, unverified, and the student lands on the dashboard."""
        with patch('accounts.views.hash_uploaded_file', side_effect=['a' * 64, OSError('disk')]):
            response = self.client.post(reverse('signup'), signup_payload())
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertFalse(User.objects.get(student_number='220404773').is_verified)
