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

from accounts.models import ReviewRequest, User
from accounts.tests_identity import ocr_result, proof, signup_payload


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


class PreviewRejectsOldDocumentsTests(TestCase):
    """Choosing the file runs the preview; an old proof is turned away there,
    before the student fills in the rest of the form."""

    def setUp(self):
        cache.clear()

    def _preview(self, result):
        with patch('accounts.views.process_registration_proof', return_value=result):
            return self.client.post(reverse('preview_registration_proof'), {'registration_proof': proof()})

    def test_an_expired_document_is_rejected_at_once(self):
        result = ocr_result('220404773')
        result['period'] = '01-Jan-2022 to 31-Dec-2022'
        response = self._preview(result)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertIn('expired on 31 Dec 2022', response.json()['message'])

    def test_an_expired_document_is_not_kept_for_the_signup_step(self):
        from accounts.views import OCR_PREVIEW_SESSION_KEY

        result = ocr_result('220404773')
        result['period'] = '01-Jan-2022 to 31-Dec-2022'
        self._preview(result)
        self.assertNotIn(OCR_PREVIEW_SESSION_KEY, self.client.session)

    def test_an_implausible_date_is_rejected_at_once(self):
        result = ocr_result('220404773')
        far = timezone.now().date() + timedelta(days=365 * 5)
        result['period'] = f'01-Jan-2026 to {far:%d-%b-%Y}'
        self.assertIn("doesn't look right", self._preview(result).json()['message'])

    def test_a_current_document_fills_in_the_form(self):
        data = self._preview(ocr_result('220404773')).json()
        self.assertTrue(data['success'])
        self.assertEqual(data['student_number'], '220404773')

    def test_a_doubtful_reading_is_not_rejected_on_its_date(self):
        """A low-confidence read may have the date wrong: the student carries
        on and, at signup, it goes to a person instead."""
        result = ocr_result('220404773', confidence=0.6)
        result['period'] = '01-Jan-2022 to 31-Dec-2022'
        self.assertTrue(self._preview(result).json()['success'])


class MessagesAppearAsPopUpsTests(TestCase):
    def test_a_message_is_shown_as_a_toast_over_the_page(self):
        cache.clear()
        result = ocr_result('220404773')
        result['period'] = '01-Jan-2020 to 31-Dec-2020'
        with patch('accounts.views.process_registration_proof', return_value=result):
            response = self.client.post(reverse('signup'), signup_payload())
        content = response.content.decode()
        self.assertRegex(content, r'<div id="toast-stack" class="toast-stack">\s*<div class="app-toast app-toast-warning card shadow"')
        self.assertIn('role="alert"', content)
        self.assertIn('js/toasts', content)
        self.assertNotIn('alert-dismissible', content)


@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class DetailsMustMatchTheDocumentTests(TestCase):
    """A genuine proof of registration with someone else's details typed in:
    the student is asked twice to correct them, with no account created;
    after that a staff member decides."""

    def setUp(self):
        cache.clear()

    def _signup(self, result, **fields):
        with patch('accounts.views.process_registration_proof', return_value=result) as read:
            response = self.client.post(reverse('signup'), signup_payload(**fields))
        self.read = read
        return response

    def test_a_different_student_number_is_sent_back_for_correction(self):
        response = self._signup(ocr_result('220404999'), student_number='220404773')
        self.assertContains(response, "match the student number on your proof of registration (220404999)")
        self.assertContains(response, 'attempt 1 of 2')
        self.assertFalse(User.objects.exists())

    def test_a_different_name_is_sent_back_for_correction(self):
        response = self._signup(ocr_result('220404773'), first_name='Someone', last_name='Else')
        self.assertContains(response, "match the name on your proof of registration (Kekeletso Khaka)")
        self.assertFalse(User.objects.exists())

    def test_the_second_try_says_a_person_will_check_next(self):
        self._signup(ocr_result('220404773'), first_name='Someone')
        response = self._signup(ocr_result('220404773'), first_name='Someone')
        self.assertContains(response, 'attempt 2 of 2')
        self.assertContains(response, 'a staff member will check it')
        self.assertFalse(User.objects.exists())

    def test_the_third_try_goes_to_a_staff_member_without_a_card(self):
        for _ in range(3):
            self._signup(ocr_result('220404773'), first_name='Someone')
        user = User.objects.get(student_number='220404773')
        self.assertFalse(user.is_verified)
        self.assertEqual(ReviewRequest.objects.get(user=user).reason, 'name_mismatch')

    def test_a_different_number_on_the_third_try_is_a_number_review(self):
        for _ in range(3):
            self._signup(ocr_result('220404999'), student_number='220404773')
        user = User.objects.get(student_number='220404773')
        self.assertEqual(ReviewRequest.objects.get(user=user).reason, 'number_mismatch')

    def test_correcting_the_details_verifies_the_student(self):
        self._signup(ocr_result('220404773'), first_name='Someone')
        self._signup(ocr_result('220404773'))
        self.assertTrue(User.objects.get(student_number='220404773').is_verified)

    def test_a_different_email_on_the_document_is_sent_back(self):
        result = ocr_result('220404773')
        result['email'] = '220404999@tut4life.ac.za'
        response = self._signup(result)
        self.assertContains(response, 'match the email on your proof of registration')

    def test_a_retry_does_not_read_the_document_again(self):
        """The same file is uploaded again - its reading is reused."""
        from accounts.views import OCR_PREVIEW_SESSION_KEY

        self._signup(ocr_result('220404773'), first_name='Someone')
        self.assertIn(OCR_PREVIEW_SESSION_KEY, self.client.session)
        self._signup(ocr_result('220404773'))
        self.read.assert_not_called()
        self.assertTrue(User.objects.get(student_number='220404773').is_verified)

    def test_honest_differences_in_how_a_name_is_written_are_accepted(self):
        result = ocr_result('220404773')
        result['full_name'] = 'MR KEKELETSO THABO KHAKA'
        self._signup(result, first_name='kekeletso', last_name='Khaka')
        self.assertTrue(User.objects.get(student_number='220404773').is_verified)


class NameComparisonTests(TestCase):
    def test_forgiving_about_case_accents_order_titles_middle_names_and_initials(self):
        from accounts.services import name_matches_document

        for typed, document in (
            ('Kekeletso', 'KHAKA KEKELETSO'),
            ('Khaka', 'Mr Kekeletso Khaka'),
            ('Kekeletso', 'Kekeletso Thabo Khaka'),
            ('Kekeletso', 'K Khaka'),
            ('Zoë', 'ZOE Mokoena'),
            ('Anything', ''),  # nothing read from the document: not compared
        ):
            with self.subTest(typed=typed, document=document):
                self.assertTrue(name_matches_document(typed, document))

    def test_a_different_name_does_not_match(self):
        from accounts.services import name_matches_document

        for typed, document in (('Someone', 'Kekeletso Khaka'), ('Khaka-Mokoena', 'Kekeletso Khaka'), ('', 'Kekeletso Khaka')):
            with self.subTest(typed=typed):
                self.assertFalse(name_matches_document(typed, document))
