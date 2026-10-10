"""The student is only told to check their inbox when an email was actually
sent. With the console backend (no email service configured) the old
message said it was sent anyway, which hid a missing email setup."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User


class ConfirmationEmailMessageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )
        self.client.force_login(self.student)

    def _resend(self):
        return self.client.post(reverse('resend_confirmation'), follow=True)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_a_delivering_backend_says_check_your_inbox(self):
        self.assertContains(self._resend(), 'please check your inbox')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_the_console_backend_says_nothing_was_sent(self):
        response = self._resend()
        self.assertNotContains(response, 'please check your inbox')
        self.assertContains(response, 'no confirmation email was sent')
