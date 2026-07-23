from datetime import date, timedelta

from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import StudentUser
from .models import StudentCard
from .utils import (
    build_card_verification_url,
    classify_tut_proof_of_registration,
    generate_student_card,
    normalize_llm_output,
    parse_registration_end_date,
    resolve_document_classification,
)


def make_user(email='student@tut4life.ac.za', student_number='123456789', password='TestPass123!', **extra):
    user = StudentUser(email=email, student_number=student_number, first_name='Test', last_name='Student', **extra)
    user.username = email
    user.set_password(password)
    user.save()
    return user


REAL_DOCUMENT_TEXT = """
TSHWANE UNIVERSITY OF TECHNOLOGY
TO WHOM IT MAY CONCERN
It is hereby confirmed that JOHN SMITH, Student Number 220404773
ID Number 9606215359089
Qualification: ADRS20 AdvDip (Computer Science)
Registered From 03-Jan-2026 to 31-Dec-2026
Campus and Registration Type: Full Time
REGISTRAR
"""


class ClassificationThresholdTests(TestCase):
    """Regression tests for switching the rule-based classifier from
    'must contain 3 exact phrases' to 'overall match score >= 0.8' -
    the old rule rejected real documents on minor OCR noise."""

    def test_document_with_all_indicators_passes(self):
        result = classify_tut_proof_of_registration(REAL_DOCUMENT_TEXT)
        self.assertTrue(result['is_tut_proof_of_registration'])
        self.assertGreaterEqual(result['classification_confidence'], 0.8)

    def test_ocr_noise_on_one_phrase_still_passes_at_8_of_9(self):
        noisy_text = REAL_DOCUMENT_TEXT.replace('Registered From', 'Reg1stered From')
        result = classify_tut_proof_of_registration(noisy_text)
        self.assertTrue(result['is_tut_proof_of_registration'])

    def test_weak_match_fails(self):
        result = classify_tut_proof_of_registration('Student Number 12345 Qualification: BSc Registrar')
        self.assertFalse(result['is_tut_proof_of_registration'])

    def test_empty_text_fails_cleanly(self):
        result = classify_tut_proof_of_registration('')
        self.assertFalse(result['is_tut_proof_of_registration'])
        self.assertEqual(result['classification_confidence'], 0.0)


class LLMClassificationResolutionTests(TestCase):
    """The combined LLM classify+extract call is preferred; falls back to
    the rule-based classifier when unavailable, and the 0.8 confidence
    threshold gates the result regardless of what the LLM's own boolean says."""

    def test_llm_high_confidence_is_used(self):
        llm_result = normalize_llm_output({
            'is_tut_proof_of_registration': True,
            'classification_confidence': 0.92,
            'classification_reason': 'Looks legitimate.',
        })
        resolved = resolve_document_classification('irrelevant', llm_result)
        self.assertTrue(resolved['is_tut_proof_of_registration'])
        self.assertEqual(resolved['classification_confidence'], 0.92)

    def test_llm_low_confidence_gated_false_even_if_llm_says_true(self):
        llm_result = normalize_llm_output({
            'is_tut_proof_of_registration': True,
            'classification_confidence': 0.5,
        })
        resolved = resolve_document_classification('irrelevant', llm_result)
        self.assertFalse(resolved['is_tut_proof_of_registration'])

    def test_llm_percentage_confidence_normalized_to_0_1_scale(self):
        llm_result = normalize_llm_output({'classification_confidence': 92})
        resolved = resolve_document_classification('irrelevant', llm_result)
        self.assertEqual(resolved['classification_confidence'], 0.92)

    def test_falls_back_to_rule_based_when_llm_unavailable(self):
        resolved = resolve_document_classification(REAL_DOCUMENT_TEXT, None)
        self.assertTrue(resolved['is_tut_proof_of_registration'])


class RegistrationEndDateParsingTests(TestCase):
    def test_parses_dd_mon_yyyy_period(self):
        result = parse_registration_end_date('03-Jan-2026 to 31-Dec-2026')
        self.assertEqual(result, date(2026, 12, 31))

    def test_parses_year_only_period(self):
        result = parse_registration_end_date('2023 to 2024')
        self.assertEqual(result, date(2024, 12, 31))

    def test_returns_none_for_unparseable_text(self):
        self.assertIsNone(parse_registration_end_date('nonsense'))

    def test_returns_none_for_empty_input(self):
        self.assertIsNone(parse_registration_end_date(None))


class CardExpiryTests(TestCase):
    """Card expiry follows the student's actual registration period when
    known, instead of always being a flat 365 days from issue."""

    def test_expiry_uses_registration_end_date_when_present(self):
        user = make_user(registration_end_date=date(2026, 12, 31))
        card = generate_student_card(user)
        self.assertEqual(card.expiry_date, date(2026, 12, 31))

    def test_expiry_falls_back_to_one_year_when_unknown(self):
        user = make_user(registration_end_date=None)
        card = generate_student_card(user)
        expected = date.today() + timedelta(days=365)
        self.assertEqual(card.expiry_date, expected)


class AdminVerificationCardGenerationTests(TestCase):
    """Manually checking is_verified in the Django admin is a soft approval
    - it should generate the student's card the same way OCR
    auto-verification does in signup()."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.middleware import MessageMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware
        from accounts.admin import StudentUserAdmin

        self.staff = make_user(email='admin2@tut4life.ac.za', student_number='staffnum2', is_staff=True, is_superuser=True)
        self.student = make_user(email='pending@tut4life.ac.za', student_number='888777666')
        self.model_admin = StudentUserAdmin(StudentUser, AdminSite())

        factory = RequestFactory()
        self.request = factory.get('/admin/')
        self.request.user = self.staff
        SessionMiddleware(lambda r: None).process_request(self.request)
        MessageMiddleware(lambda r: None).process_request(self.request)

    def test_checking_is_verified_generates_card(self):
        self.student.is_verified = True
        self.model_admin.save_model(self.request, self.student, form=None, change=True)
        self.assertTrue(StudentCard.objects.filter(user=self.student).exists())

    def test_unverified_does_not_generate_card(self):
        self.model_admin.save_model(self.request, self.student, form=None, change=True)
        self.assertFalse(StudentCard.objects.filter(user=self.student).exists())

    def test_new_student_added_pre_verified_gets_a_card(self):
        """change=False (the admin Add form) must generate a card too, not
        just change=True (editing an existing student)."""
        new_student = StudentUser(
            email='newlyadded@tut4life.ac.za', student_number='777666555',
            first_name='New', last_name='Student', username='newlyadded@tut4life.ac.za',
            is_verified=True,
        )
        new_student.set_password('TestPass123!')
        self.model_admin.save_model(self.request, new_student, form=None, change=False)
        self.assertTrue(StudentCard.objects.filter(user=new_student).exists())


class StudentCardAdminTests(TestCase):
    """Manually adding a StudentCard directly in admin should auto-generate
    a unique card_number and QR code, the same way issue_date auto-
    populates - these aren't meant to be typed in by hand."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        from cards.admin import StudentCardAdmin

        self.staff = make_user(email='admin3@tut4life.ac.za', student_number='staffnum3', is_staff=True, is_superuser=True)
        self.student = make_user(email='cardless@tut4life.ac.za', student_number='444555666')
        self.model_admin = StudentCardAdmin(StudentCard, AdminSite())

        factory = RequestFactory()
        self.request = factory.get('/admin/')
        self.request.user = self.staff

    def test_manually_added_card_gets_number_and_qr_code(self):
        card = StudentCard(user=self.student)
        self.model_admin.save_model(self.request, card, form=None, change=False)

        card.refresh_from_db()
        self.assertTrue(card.card_number)
        self.assertTrue(card.qr_code)

    def test_manually_provided_expiry_date_is_not_overridden(self):
        custom_expiry = date(2099, 1, 1)
        card = StudentCard(user=self.student, expiry_date=custom_expiry)
        self.model_admin.save_model(self.request, card, form=None, change=False)

        card.refresh_from_db()
        self.assertEqual(card.expiry_date, custom_expiry)


class PermissionBoundaryTests(TestCase):
    """Staff/admin accounts should never land on student-only pages, and
    students should never reach staff-only reporting pages."""

    def setUp(self):
        self.student = make_user()
        self.staff = make_user(email='admin@tut4life.ac.za', student_number='staffnum', is_staff=True)

    def test_staff_redirected_away_from_student_dashboard(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, reverse('performance_dashboard'))

    def test_student_redirected_to_login_from_reports(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('performance_dashboard'))
        self.assertNotEqual(response.status_code, 200)

    def test_anonymous_redirected_to_login_from_dashboard(self):
        response = self.client.get(reverse('dashboard'))
        self.assertIn(reverse('login'), response.url)


class PublicVerifyTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.card = generate_student_card(self.user)

    def test_valid_card_number_returns_200(self):
        response = self.client.get(reverse('verify_card_public', args=[self.card.card_number]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.student_number)

    def test_unknown_card_number_returns_404(self):
        response = self.client.get(reverse('verify_card_public', args=['DOES-NOT-EXIST']))
        self.assertEqual(response.status_code, 404)

    def test_qr_url_is_https_when_behind_a_proxy(self):
        """Regression test: without SECURE_PROXY_SSL_HEADER, this would
        generate an http:// link even when served over https:// behind
        DigitalOcean App Platform's load balancer."""
        factory = RequestFactory()
        request = factory.get('/', HTTP_X_FORWARDED_PROTO='https', SERVER_NAME='127.0.0.1')
        url = build_card_verification_url(self.card.card_number, request)
        self.assertTrue(url.startswith('https://'))
