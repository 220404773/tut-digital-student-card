"""The Content-Security-Policy only allows inline scripts that carry the
request's nonce, and no inline event handlers at all - so every inline script
must be nonced, and handlers like onsubmit="..." must not come back.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from cards.models import StudentCard

SCRIPT_TAG = re.compile(r'<script\b([^>]*)>', re.IGNORECASE)
INLINE_HANDLER = re.compile(r'\son[a-z]+\s*=\s*["\']', re.IGNORECASE)


def policy_header(response):
    return response.headers.get('Content-Security-Policy') or response.headers.get(
        'Content-Security-Policy-Report-Only', '')


class CspHeaderTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', is_verified=True, email_confirmed=True,
        )
        StudentCard.objects.create(user=self.student, card_number='TUT-TEST-0001')

    def assert_every_inline_script_is_nonced(self, response):
        html = response.content.decode()
        match = re.search(r"'nonce-([^']+)'", policy_header(response))
        self.assertIsNotNone(match, 'policy has no nonce')
        nonce = match.group(1)
        for attributes in SCRIPT_TAG.findall(html):
            if 'src=' not in attributes:
                self.assertIn(f'nonce="{nonce}"', attributes)

    def test_public_pages_send_the_policy_and_nonce_their_scripts(self):
        for name in ('home', 'login', 'signup', 'privacy'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertIn("script-src 'self'", policy_header(response))
                self.assert_every_inline_script_is_nonced(response)

    def test_student_pages_nonce_their_scripts(self):
        self.client.force_login(self.student)
        for name in ('dashboard', 'profile'):
            with self.subTest(page=name):
                self.assert_every_inline_script_is_nonced(self.client.get(reverse(name)))

    def test_pages_load_no_third_party_scripts_or_styles(self):
        """Bootstrap and Font Awesome are self-hosted: a CDN blocked by a
        network or a managed browser left pages unstyled, and every page load
        sent the visitor's IP address to the CDN."""
        html = self.client.get(reverse('home')).content.decode()
        for tag in re.findall(r'<(?:script|link)\b[^>]*>', html, re.IGNORECASE):
            self.assertNotRegex(tag, r'(src|href)="https?://', tag)
        self.assertNotIn('https://', policy_header(self.client.get(reverse('home'))))

    def test_each_request_gets_a_fresh_nonce(self):
        first = policy_header(self.client.get(reverse('home')))
        second = policy_header(self.client.get(reverse('home')))
        self.assertNotEqual(first, second)

    def test_starts_in_report_only_mode(self):
        response = self.client.get(reverse('home'))
        self.assertIn('Content-Security-Policy-Report-Only', response.headers)
        self.assertNotIn('Content-Security-Policy', response.headers)

    def test_can_be_switched_to_enforcing(self):
        with override_settings(CONTENT_SECURITY_POLICY=settings.CONTENT_SECURITY_POLICY_REPORT_ONLY,
                               CONTENT_SECURITY_POLICY_REPORT_ONLY=None):
            response = self.client.get(reverse('home'))
        self.assertIn('Content-Security-Policy', response.headers)


class NoInlineEventHandlersTests(TestCase):
    def test_no_template_uses_inline_event_handlers(self):
        """The policy blocks them; use data-confirm / a static script instead."""
        offenders = []
        for template in Path(settings.BASE_DIR).glob('**/templates/**/*.html'):
            if 'venv' in template.parts:
                continue
            for number, line in enumerate(template.read_text(encoding='utf-8').splitlines(), 1):
                if INLINE_HANDLER.search(line):
                    offenders.append(f'{template.relative_to(settings.BASE_DIR)}:{number}')
        self.assertEqual(offenders, [])

    def test_confirm_text_with_an_apostrophe_is_escaped_as_plain_attribute_text(self):
        """A name like O'Brien used to be decoded back into a raw quote inside
        an inline JavaScript string - breaking the prompt, or worse."""
        staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        student = User.objects.create_user(
            username='220404888@tut4life.ac.za', email='220404888@tut4life.ac.za',
            student_number='220404888', password='StrongPass123!',
            first_name="Sean", last_name="O'Brien", is_verified=True,
        )
        StudentCard.objects.create(user=student, card_number='TUT-TEST-0002', status='expired')
        self.client.force_login(staff)

        response = self.client.get(reverse('report_expired_cards'))

        self.assertContains(response, 'data-confirm="Reissue a new card number and QR code for Sean O&#x27;Brien?')
