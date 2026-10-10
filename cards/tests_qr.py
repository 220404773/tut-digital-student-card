"""QR codes are drawn from the card number each time they're shown, so they
always point at the current address and a reissued card's old code simply
stops existing - nothing is stored to go stale or be left behind."""
import cv2
import numpy as np
from unittest.mock import patch

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
        self.assertTrue(decode_qr(response.content).startswith(
            'http://testserver' + reverse('verify_card_public', args=['TUT1ABCDEF12']) + '?live=',
        ))

    @override_settings(SITE_URL='https://tut-card.ondigitalocean.app/')
    def test_site_url_pins_the_address_whatever_address_the_page_was_opened_on(self):
        response = self.client.get(reverse('card_qr_image'))
        self.assertTrue(decode_qr(response.content).startswith(
            'https://tut-card.ondigitalocean.app' + reverse('verify_card_public', args=['TUT1ABCDEF12']) + '?live=',
        ))

    def test_qr_image_is_never_cached(self):
        """It identifies the card, and after a reissue the old code must not
        linger in the browser."""
        self.assertEqual(self.client.get(reverse('card_qr_image'))['Cache-Control'], 'no-store')

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


class LiveQrCodeTests(TestCase):
    """The card on screen carries a signed code that expires within a minute,
    so a screenshot of someone else's card reads as an old code to a guard."""

    def setUp(self):
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', is_verified=True, email_confirmed=True,
        )
        StudentCard.objects.create(user=self.student, card_number='TUT1LIVE0001')
        self.client.force_login(self.student)

    def _scan(self, url):
        """Open the address in a QR code, as a guard's phone would."""
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        self.client.logout()
        return self.client.get(parts.path + ('?' + parts.query if parts.query else ''))

    def test_the_live_card_verifies_as_live(self):
        url = decode_qr(self.client.get(reverse('card_qr_image')).content)
        self.assertContains(self._scan(url), 'LIVE CODE')

    def test_a_screenshot_older_than_a_minute_shows_as_an_old_code(self):
        url = decode_qr(self.client.get(reverse('card_qr_image')).content)
        import time
        later = time.time() + 61
        with patch('django.core.signing.time.time', return_value=later):
            response = self._scan(url)
        self.assertContains(response, 'OLD CODE')
        self.assertNotContains(response, 'LIVE CODE')

    def test_the_downloaded_qr_is_a_saved_code(self):
        url = decode_qr(self.client.get(reverse('download_qr')).content)
        self.assertNotIn('live=', url)
        self.assertContains(self._scan(url), 'SAVED CODE')

    def test_a_tampered_code_is_not_genuine(self):
        url = decode_qr(self.client.get(reverse('card_qr_image')).content)
        self.assertContains(self._scan(url[:-3] + 'abc'), 'NOT A GENUINE CODE')

    def test_one_cards_live_code_does_not_work_for_another_card(self):
        other = User.objects.create_user(
            username='220404999@tut4life.ac.za', email='220404999@tut4life.ac.za',
            student_number='220404999', password='StrongPass123!',
        )
        StudentCard.objects.create(user=other, card_number='TUT1LIVE0002')
        url = decode_qr(self.client.get(reverse('card_qr_image')).content)
        self.assertContains(self._scan(url.replace('TUT1LIVE0001/', 'TUT1LIVE0002/')), 'NOT A GENUINE CODE')

    def test_each_qr_request_carries_a_new_code(self):
        import time
        first = decode_qr(self.client.get(reverse('card_qr_image')).content)
        with patch('django.core.signing.time.time', return_value=time.time() + 30):
            second = decode_qr(self.client.get(reverse('card_qr_image')).content)
        self.assertNotEqual(first, second)

    def test_the_dashboard_refreshes_the_code_and_shows_the_live_clock(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-refresh-seconds="30"')
        self.assertContains(response, 'data-live-clock')
        self.assertContains(response, 'js/live-card')
        # Explains the tilt check as an anti-screenshot feature, not a setting.
        self.assertContains(response, 'Tilt to check: tap to turn on')
