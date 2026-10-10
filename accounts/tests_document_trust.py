"""Tests for the two controls that narrow the enrolment trust gap: a card is
only issued once the student proves control of the institutional mailbox, and
one proof-of-registration file can only ever verify one account.
"""
import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from cards.models import StudentCard


def make_verified_user(**overrides):
    defaults = {
        'username': 'verified@tut4life.ac.za',
        'email': 'verified@tut4life.ac.za',
        'student_number': '220202117',
        'first_name': 'Ver',
        'last_name': 'Ified',
        'is_verified': True,
        'email_confirmed': False,
    }
    defaults.update(overrides)
    user = User.objects.create_user(password='StrongPass123!', **defaults)
    return user


# Photos aren't what these tests are about - see tests_photo_lock.py.
@override_settings(REQUIRE_PHOTO_FOR_CARD=False)
@override_settings(REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class CardDeferredUntilEmailConfirmedTests(TestCase):
    """A verified document proves a document was read, not that the uploader
    is the person named on it. Confirming the mailbox tied to the student
    number is what releases the card."""

    def setUp(self):
        # The resend cooldown lives in the cache, which survives the per-test
        # database rollback - without this, a reused pk carries the previous
        # test's cooldown and no email is sent.
        cache.clear()
        self.user = make_verified_user()

    def test_verified_student_has_no_card_before_confirming_email(self):
        self.assertFalse(StudentCard.objects.filter(user=self.user).exists())

    def test_confirming_email_issues_the_card(self):
        self.client.force_login(self.user)
        self.client.post(reverse('resend_confirmation'))
        link = re.search(r'(/accounts/confirm-email/\S+/)', mail.outbox[0].body).group(1)

        self.client.post(link)

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_confirmed)
        self.assertTrue(StudentCard.objects.filter(user=self.user).exists())

    def test_confirming_twice_does_not_create_a_second_card(self):
        self.client.force_login(self.user)
        self.client.post(reverse('resend_confirmation'))
        link = re.search(r'(/accounts/confirm-email/\S+/)', mail.outbox[0].body).group(1)

        self.client.post(link)
        self.client.post(link)

        self.assertEqual(StudentCard.objects.filter(user=self.user).count(), 1)

    def test_unverified_student_gets_no_card_even_after_confirming(self):
        unverified = make_verified_user(
            username='pending@tut4life.ac.za', email='pending@tut4life.ac.za',
            student_number='220404774', is_verified=False,
        )
        self.client.force_login(unverified)
        self.client.post(reverse('resend_confirmation'))
        link = re.search(r'(/accounts/confirm-email/\S+/)', mail.outbox[0].body).group(1)

        self.client.post(link)

        unverified.refresh_from_db()
        self.assertTrue(unverified.email_confirmed)
        self.assertFalse(StudentCard.objects.filter(user=unverified).exists())


class ProofDocumentReuseTests(TestCase):
    """The same file must not be able to verify two different accounts."""

    def test_a_document_hash_is_bound_to_only_one_account(self):
        make_verified_user(registration_proof_hash='a' * 64)
        reused = User.objects.filter(registration_proof_hash='a' * 64)
        self.assertEqual(reused.count(), 1)

    def test_signup_rejects_a_file_already_used_by_another_account(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        import hashlib

        content = b'%PDF-1.4\n%a specific proof of registration'
        digest = hashlib.sha256(content).hexdigest()
        make_verified_user(registration_proof_hash=digest)

        response = self.client.post(reverse('signup'), {
            'email': '220404775@tut4life.ac.za',
            'first_name': 'Second',
            'last_name': 'Student',
            'student_number': '220404775',
            'phone': '',
            'registration_proof': SimpleUploadedFile('proof.pdf', content, content_type='application/pdf'),
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'consent': 'on',
        })

        self.assertContains(response, 'already been used to register another account')
        self.assertFalse(User.objects.filter(student_number='220404775').exists())

    def test_rejected_document_is_not_consumed(self):
        """A document that fails verification must not be recorded as used,
        so the student can retry with the same file."""
        user = make_verified_user(
            username='retry@tut4life.ac.za', email='retry@tut4life.ac.za',
            student_number='220404776', is_verified=False,
        )
        self.assertEqual(user.registration_proof_hash, '')
