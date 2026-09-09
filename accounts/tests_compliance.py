import os

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import StudentUser


class LegalPagesTest(TestCase):
    def test_all_three_legal_pages_load_without_login(self):
        for name in ('privacy', 'terms', 'cookies'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_legal_pages_are_linked_from_every_page_footer(self):
        response = self.client.get(reverse('home'))
        for name in ('privacy', 'terms', 'cookies'):
            self.assertContains(response, reverse(name))

    def test_privacy_policy_discloses_third_party_processors(self):
        response = self.client.get(reverse('privacy'))
        for processor in ('NVIDIA', 'Azure', 'Google Wallet', 'DigitalOcean', 'jsDelivr'):
            self.assertContains(response, processor)


class SignupConsentTest(TestCase):
    def _payload(self, **overrides):
        data = {
            'email': 'consent@tut4life.ac.za',
            'first_name': 'Con',
            'last_name': 'Sent',
            'student_number': '444555666',
            'phone': '',
            'registration_proof': SimpleUploadedFile(
                'proof.pdf', b'%PDF-1.4\n%placeholder', content_type='application/pdf'
            ),
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'consent': 'on',
        }
        data.update(overrides)
        return data

    def test_signup_without_consent_is_rejected_and_creates_no_account(self):
        data = self._payload()
        del data['consent']
        response = self.client.post(reverse('signup'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StudentUser.objects.filter(student_number='444555666').exists())

    def test_signup_with_consent_records_the_timestamp(self):
        self.client.post(reverse('signup'), self._payload())
        user = StudentUser.objects.get(student_number='444555666')
        self.assertIsNotNone(user.consent_given_at)

    def test_signup_page_links_to_privacy_and_terms(self):
        response = self.client.get(reverse('signup'))
        self.assertContains(response, reverse('privacy'))
        self.assertContains(response, reverse('terms'))


class AccessibilityTest(TestCase):
    def test_skip_link_and_main_landmark_present(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Skip to main content')
        self.assertContains(response, 'id="main-content"')

    def test_icon_only_buttons_have_accessible_names(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'aria-label="Toggle navigation menu"')

    def test_errored_field_is_marked_invalid_and_linked_to_its_error(self):
        response = self.client.post(reverse('signup'), {
            'email': 'not-an-email',
            'first_name': '',
            'last_name': '',
            'student_number': '',
            'password1': 'x',
            'password2': 'y',
        })
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'aria-describedby="id_email_error"')
        self.assertContains(response, 'id="id_email_error"')


class NoLowContrastCombosTest(TestCase):
    """The dashboard previously rendered white text on Bootstrap's amber and
    cyan backgrounds (~1.6:1 and ~2.0:1 against a 4.5:1 AA requirement)."""

    def test_dashboard_template_has_no_white_on_warning_or_info(self):
        template = os.path.join(settings.BASE_DIR, 'cards', 'templates', 'cards', 'performance_dashboard.html')
        with open(template, encoding='utf-8') as f:
            markup = f.read()
        self.assertNotIn('bg-info text-white', markup)
        self.assertNotIn('bg-warning text-dark{% else %}bg-danger{% endif %} text-white', markup)
        self.assertNotIn('class="text-warning"', markup)
