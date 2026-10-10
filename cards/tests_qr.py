"""QR codes are drawn from the card number each time they're shown, so they
always point at the current address and a reissued card's old code simply
stops existing - nothing is stored to go stale or be left behind."""
import cv2
import numpy as np
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from cards.models import StudentCard


def decode_qr(png_bytes):
    image = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    return text


class CardQrCodeTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', is_verified=True, email_confirmed=True,
        )
        self.card = StudentCard.objects.create(user=self.student, card_number='TUT1ABCDEF12')
        self.client.force_login(self.student)

    def test_qr_image_is_a_png_pointing_at_the_cards_verification_page(self):
        response = self.client.get(reverse('card_qr_image'))
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(
            decode_qr(response.content),
            'http://testserver' + reverse('verify_card_public', args=['TUT1ABCDEF12']),
        )

    @override_settings(SITE_URL='https://tut-card.ondigitalocean.app/')
    def test_site_url_pins_the_address_whatever_address_the_page_was_opened_on(self):
        response = self.client.get(reverse('card_qr_image'))
        self.assertEqual(
            decode_qr(response.content),
            'https://tut-card.ondigitalocean.app' + reverse('verify_card_public', args=['TUT1ABCDEF12']),
        )

    def test_qr_image_is_not_kept_by_shared_caches(self):
        self.assertIn('private', self.client.get(reverse('card_qr_image'))['Cache-Control'])

    def test_download_contains_the_same_code(self):
        response = self.client.get(reverse('download_qr'))
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('/TUT1ABCDEF12/', decode_qr(response.content))

    def test_a_reissued_card_number_changes_the_code_with_nothing_to_clean_up(self):
        self.card.card_number = 'TUT1NEWNUM34'
        self.card.save()
        self.assertIn('/TUT1NEWNUM34/', decode_qr(self.client.get(reverse('card_qr_image')).content))

    def test_signed_out_visitors_cannot_fetch_a_qr_image(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('card_qr_image')).status_code, 302)

    def test_dashboard_shows_the_on_demand_image(self):
        self.assertContains(self.client.get(reverse('dashboard')), reverse('card_qr_image'))
