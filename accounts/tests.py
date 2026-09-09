from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.forms import CustomUserCreationForm
from cards.models import StudentCard
from .models import StudentUser


def make_user(email='220404773@tut4life.ac.za', student_number='220404773', password='TestPass123!', **extra):
    user = StudentUser(email=email, student_number=student_number, first_name='Test', last_name='Student', **extra)
    user.username = email  # signup() sets this explicitly - see test below for why that matters
    user.set_password(password)
    user.save()
    return user


class SignupUsernameTests(TestCase):
    """Regression tests for the bug where signup never set `username`, so
    every account defaulted to '' and only one could ever exist before
    hitting the unique constraint."""

    def test_signup_sets_username_to_email(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        proof = SimpleUploadedFile('proof.pdf', b'%PDF-1.4\n%minimal placeholder pdf content', content_type='application/pdf')
        response = self.client.post(reverse('signup'), {
            'email': 'newstudent@tut4life.ac.za',
            'first_name': 'New',
            'last_name': 'Student',
            'student_number': '111222333',
            'phone': '',
            'registration_proof': proof,
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'consent': 'on',
        })
        user = StudentUser.objects.get(student_number='111222333')
        self.assertEqual(user.username, 'newstudent@tut4life.ac.za')

    def test_two_signups_without_registration_proof_dont_collide(self):
        """Two accounts created without ever filling in username manually
        must not collide on a blank username unique constraint."""
        make_user(email='a@tut4life.ac.za', student_number='1')
        make_user(email='b@tut4life.ac.za', student_number='2')
        self.assertEqual(StudentUser.objects.count(), 2)


class SignupDurationTests(TestCase):
    """signup_duration_seconds should measure from when the student picked
    a file (client-reported timestamp), not just this request's backend
    processing time."""

    def test_uses_client_reported_upload_start_time(self):
        import time
        from django.core.files.uploadedfile import SimpleUploadedFile
        from cards.models import SignupMetrics

        proof = SimpleUploadedFile('proof.pdf', b'%PDF-1.4\n%minimal placeholder pdf content', content_type='application/pdf')
        upload_start_ms = int(time.time() * 1000) - 15000  # 15s before this request

        self.client.post(reverse('signup'), {
            'email': 'timed@tut4life.ac.za',
            'first_name': 'Timed',
            'last_name': 'Student',
            'student_number': '999888777',
            'phone': '',
            'upload_start_time': upload_start_ms,
            'registration_proof': proof,
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'consent': 'on',
        })

        metrics = SignupMetrics.objects.get(user__student_number='999888777')
        self.assertGreaterEqual(metrics.signup_duration_seconds, 15)

    def test_missing_upload_start_time_falls_back_to_request_time(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from cards.models import SignupMetrics

        proof = SimpleUploadedFile('proof.pdf', b'%PDF-1.4\n%minimal placeholder pdf content', content_type='application/pdf')
        self.client.post(reverse('signup'), {
            'email': 'notiming@tut4life.ac.za',
            'first_name': 'No',
            'last_name': 'Timing',
            'student_number': '555444333',
            'phone': '',
            'registration_proof': proof,
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'consent': 'on',
        })

        metrics = SignupMetrics.objects.get(user__student_number='555444333')
        self.assertLess(metrics.signup_duration_seconds, 5)


class DuplicateAccountRedirectTests(TestCase):
    """Signing up with an email/student number that already exists should
    redirect to login (with the email prefilled) instead of re-showing a
    failed signup form."""

    def setUp(self):
        self.existing = make_user()

    def _post_signup(self, **overrides):
        data = {
            'email': 'someone-else@tut4life.ac.za',
            'first_name': 'Some',
            'last_name': 'One',
            'student_number': '999999999',
            'phone': '',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'consent': 'on',
        }
        data.update(overrides)
        return self.client.post(reverse('signup'), data)

    def test_duplicate_email_redirects_to_login(self):
        response = self._post_signup(email=self.existing.email)
        self.assertRedirects(response, f"{reverse('login')}?email={self.existing.email.replace('@', '%40')}")
        self.assertEqual(StudentUser.objects.filter(email=self.existing.email).count(), 1)

    def test_duplicate_student_number_redirects_to_login(self):
        response = self._post_signup(student_number=self.existing.student_number)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertEqual(StudentUser.objects.filter(student_number=self.existing.student_number).count(), 1)


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()

    def test_login_is_case_insensitive(self):
        response = self.client.post(reverse('login'), {
            'email': self.user.email.upper(),
            'password': 'TestPass123!',
        }, follow=True)
        self.assertRedirects(response, reverse('dashboard'))

    def test_unknown_email_shows_field_specific_error(self):
        response = self.client.post(reverse('login'), {
            'email': 'nobody@tut4life.ac.za',
            'password': 'whatever',
        })
        self.assertContains(response, 'No account found with this email address.')

    def test_wrong_password_shows_field_specific_error(self):
        response = self.client.post(reverse('login'), {
            'email': self.user.email,
            'password': 'wrong-password',
        })
        self.assertContains(response, 'Incorrect password.')

    def test_staff_redirected_to_reports_not_dashboard(self):
        staff = make_user(email='admin2@tut4life.ac.za', student_number='staff1', is_staff=True)
        response = self.client.post(reverse('login'), {
            'email': staff.email,
            'password': 'TestPass123!',
        }, follow=True)
        self.assertRedirects(response, reverse('performance_dashboard'))

    def test_login_rate_limited_after_threshold(self):
        for _ in range(10):
            self.client.post(reverse('login'), {'email': 'x@tut4life.ac.za', 'password': 'wrong'})
        limited_response = self.client.post(reverse('login'), {'email': 'x@tut4life.ac.za', 'password': 'wrong'})
        self.assertContains(limited_response, 'Too many login attempts')


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_full_reset_flow_changes_password(self):
        self.client.post(reverse('password_reset'), {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 1)

        import re
        match = re.search(r'(/accounts/password-reset-confirm/\S+/)', mail.outbox[0].body)
        self.assertIsNotNone(match)

        confirm_get = self.client.get(match.group(1), follow=True)
        final_path = confirm_get.redirect_chain[-1][0]

        set_password_response = self.client.post(final_path, {
            'new_password1': 'BrandNewPass456!',
            'new_password2': 'BrandNewPass456!',
        }, follow=True)
        self.assertRedirects(set_password_response, reverse('password_reset_complete'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass456!'))

    def test_first_unregistered_attempt_shows_generic_message(self):
        response = self.client.post(reverse('password_reset'), {'email': 'nobody@tut4life.ac.za'}, follow=True)
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_second_unregistered_attempt_redirects_to_signup(self):
        self.client.post(reverse('password_reset'), {'email': 'nobody@tut4life.ac.za'})
        response = self.client.post(reverse('password_reset'), {'email': 'nobody@tut4life.ac.za'}, follow=True)
        self.assertRedirects(response, reverse('signup'))

    def test_registered_email_resets_the_failed_attempt_counter(self):
        self.client.post(reverse('password_reset'), {'email': 'nobody@tut4life.ac.za'})
        self.client.post(reverse('password_reset'), {'email': self.user.email})

        response = self.client.post(reverse('password_reset'), {'email': 'nobody@tut4life.ac.za'}, follow=True)
        self.assertRedirects(response, reverse('password_reset_done'))


class ChangePasswordTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_wrong_old_password_rejected(self):
        response = self.client.post(reverse('profile'), {
            'form_type': 'password',
            'old_password': 'not-the-real-password',
            'new_password1': 'AnotherPass789!',
            'new_password2': 'AnotherPass789!',
        })
        self.assertContains(response, 'incorrect', status_code=200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('TestPass123!'))

    def test_correct_old_password_changes_and_keeps_session(self):
        response = self.client.post(reverse('profile'), {
            'form_type': 'password',
            'old_password': 'TestPass123!',
            'new_password1': 'AnotherPass789!',
            'new_password2': 'AnotherPass789!',
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        # Session must still be authenticated - update_session_auth_hash's job.
        still_authed = self.client.get(reverse('profile'))
        self.assertEqual(still_authed.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('AnotherPass789!'))

    def test_profile_edit_does_not_touch_email(self):
        """Regression test: email was previously included in the profile
        edit form even though the template disables that input, so every
        save silently blanked it out."""
        self.client.post(reverse('profile'), {
            'first_name': 'Changed',
            'last_name': self.user.last_name,
            'phone': '',
            'date_of_birth': '',
            'gender': '',
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '220404773@tut4life.ac.za')
        self.assertEqual(self.user.first_name, 'Changed')


class FileUploadValidationTests(TestCase):
    """The registration proof upload is checked by content, not just by
    filename extension - a renamed file with the right extension but wrong
    content should be rejected."""

    def _form(self, uploaded_file):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return CustomUserCreationForm(data={
            'email': 'filetest@tut4life.ac.za',
            'first_name': 'File',
            'last_name': 'Test',
            'student_number': '555',
            'phone': '',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
        }, files={'registration_proof': uploaded_file})

    def test_rejects_file_with_mismatched_content(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake = SimpleUploadedFile('proof.pdf', b'MZ\x90\x00\x03\x00 this is not a pdf', content_type='application/pdf')
        form = self._form(fake)
        self.assertFalse(form.is_valid())
        self.assertIn('registration_proof', form.errors)

    def test_accepts_file_with_correct_signature(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        real = SimpleUploadedFile('proof.pdf', b'%PDF-1.4\n%rest of a real pdf header', content_type='application/pdf')
        form = self._form(real)
        form.is_valid()
        self.assertNotIn('registration_proof', form.errors)


class EmailConfirmationTests(TestCase):
    """Identity trust used to rest entirely on OCR reading an uploaded
    document (which can be edited/forged) - this adds an independent check
    that the student actually controls the email address on the account."""

    def setUp(self):
        cache.clear()
        self.user = make_user(email_confirmed=False)

    def test_new_users_default_to_unconfirmed(self):
        self.assertFalse(self.user.email_confirmed)

    def test_visiting_confirmation_link_does_not_confirm_on_its_own(self):
        """A bare GET (e.g. an email security scanner pre-fetching the link)
        must not confirm the email - only the button's POST does."""
        import re
        self.client.force_login(self.user)
        self.client.post(reverse('resend_confirmation'))
        self.assertEqual(len(mail.outbox), 1)

        match = re.search(r'(/accounts/confirm-email/\S+/)', mail.outbox[0].body)
        self.assertIsNotNone(match)

        response = self.client.get(match.group(1))
        self.assertContains(response, 'Confirm My Email')
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_confirmed)

    def test_valid_confirmation_link_confirms_email(self):
        import re
        self.client.force_login(self.user)
        self.client.post(reverse('resend_confirmation'))
        self.assertEqual(len(mail.outbox), 1)

        match = re.search(r'(/accounts/confirm-email/\S+/)', mail.outbox[0].body)
        self.assertIsNotNone(match)

        self.client.post(match.group(1))
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_confirmed)

    def test_invalid_token_is_rejected(self):
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse('confirm_email', args=[uid, 'bogus-token'])

        get_response = self.client.get(url)
        self.assertContains(get_response, 'invalid or has expired')

        post_response = self.client.post(url, follow=True)
        self.assertContains(post_response, 'invalid or has expired')
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_confirmed)

    def test_dashboard_shows_confirmation_banner_when_unconfirmed(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Confirm your email')

    def test_dashboard_hides_banner_once_confirmed(self):
        self.user.email_confirmed = True
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Confirm your email')

    def test_resend_is_rate_limited(self):
        self.client.force_login(self.user)
        for _ in range(5):
            self.client.post(reverse('resend_confirmation'))
        response = self.client.post(reverse('resend_confirmation'), follow=True)
        self.assertContains(response, 'Too many resend attempts')
