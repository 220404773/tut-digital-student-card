"""A student's Google Wallet pass follows their card (cards/google_wallet.py,
cards/signals.py): revoked, reissued, expired, renamed or deleted, the pass
is updated once the change is committed. Google itself is faked here."""
import io
import json
import urllib.error
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from cards.models import StudentCard

FAKE_CREDENTIALS = {'client_email': 'wallet@example.iam.gserviceaccount.com', 'private_key': 'unused'}


class FakeGoogle:
    """Records what would have been sent to the Wallet API."""

    def __init__(self, status=200):
        self.status = status
        self.patches = []

    def __call__(self, request, timeout=None):
        if self.status != 200:
            raise urllib.error.HTTPError(request.full_url, self.status, 'error', {}, io.BytesIO())
        self.patches.append((request.full_url.rsplit('/', 1)[-1], request.get_method(), json.loads(request.data)))
        return io.BytesIO(b'{}')


@override_settings(GOOGLE_WALLET_ISSUER_ID='3388000000012345', REQUIRE_PHOTO_FOR_CARD=False)
class WalletPassFollowsTheCardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', first_name='Kekeletso', last_name='Khaka',
            is_verified=True, email_confirmed=True,
        )
        self.staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        self.card = StudentCard.objects.create(user=self.student, card_number='TUT1WALLET01',
                                               expiry_date=timezone.now().date() + timedelta(days=90))
        self.google = FakeGoogle()
        for target, value in (('cards.google_wallet._load_credentials', FAKE_CREDENTIALS),
                              ('cards.google_wallet._access_token', 'token')):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('cards.google_wallet.urllib.request.urlopen', self.google)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.staff)

    def _committed(self, action):
        with self.captureOnCommitCallbacks(execute=True):
            return action()

    def test_a_revoked_card_shows_as_inactive_in_wallet(self):
        self._committed(lambda: self.client.post(reverse('admin:accounts_user_revoke_card', args=[self.student.pk])))
        object_id, method, body = self.google.patches[-1]
        self.assertEqual((object_id, method, body['state']), ('3388000000012345.TUT1WALLET01', 'PATCH', 'INACTIVE'))

    def test_a_reissued_card_retires_the_old_pass(self):
        self._committed(lambda: self.client.post(reverse('admin:accounts_user_reissue_card', args=[self.student.pk])))
        self.assertEqual(self.google.patches, [('3388000000012345.TUT1WALLET01', 'PATCH', {'state': 'INACTIVE'})])

    def test_an_expired_card_shows_as_expired_in_wallet(self):
        StudentCard.objects.filter(pk=self.card.pk).update(expiry_date=timezone.now().date() - timedelta(days=1))
        self.card.refresh_from_db()
        self._committed(self.card.refresh_status)
        self.assertEqual(self.google.patches[-1][2]['state'], 'EXPIRED')

    def test_an_approved_name_change_updates_the_name_on_the_pass(self):
        User.objects.filter(pk=self.student.pk).update(
            pending_first_name='Keke', pending_last_name='Khaka', name_change_requested_at=timezone.now())
        self._committed(lambda: self.client.post(reverse('resolve_name_change', args=[self.student.pk]),
                                                 {'action': 'approve'}))
        self.assertEqual(self.google.patches[-1][2]['header']['defaultValue']['value'], 'Keke Khaka')

    def test_a_deleted_account_retires_its_pass(self):
        self._committed(lambda: self.client.post(reverse('admin:accounts_user_delete', args=[self.student.pk]),
                                                 {'post': 'yes'}))
        self.assertIn(('3388000000012345.TUT1WALLET01', 'PATCH', {'state': 'INACTIVE'}), self.google.patches)

    def test_saving_a_card_without_changes_sends_nothing(self):
        self._committed(self.card.save)
        self.assertEqual(self.google.patches, [])

    def test_a_student_who_never_added_the_pass_is_fine(self):
        self.google.status = 404
        from cards.google_wallet import sync_wallet_pass
        self.assertFalse(sync_wallet_pass(self.card))

    def test_a_google_failure_never_stops_the_revoke(self):
        self.google.status = 500
        self._committed(lambda: self.client.post(reverse('admin:accounts_user_revoke_card', args=[self.student.pk])))
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'revoked')

    def test_the_save_link_and_the_updates_describe_the_card_the_same_way(self):
        from cards.google_wallet import _pass_details, build_google_wallet_save_url

        with patch('cards.google_wallet.jwt.encode', side_effect=lambda payload, key, algorithm: json.dumps(payload)):
            url = build_google_wallet_save_url(self.card, 'https://example.test/verify/TUT1WALLET01/')
        saved = json.loads(url.split('/save/', 1)[1])['payload']['genericObjects'][0]
        for key, value in _pass_details(self.card).items():
            self.assertEqual(saved[key], value)


class WalletOffTests(TestCase):
    @override_settings(GOOGLE_WALLET_ISSUER_ID='', GOOGLE_WALLET_CREDENTIALS_JSON='')
    def test_without_wallet_credentials_nothing_is_sent(self):
        student = User.objects.create_user(username='s@tut4life.ac.za', email='s@tut4life.ac.za',
                                           student_number='220404999', password='StrongPass123!')
        card = StudentCard.objects.create(user=student, card_number='TUT1WALLET02')
        with patch('cards.google_wallet.urllib.request.urlopen') as urlopen, \
                self.captureOnCommitCallbacks(execute=True):
            card.status = 'revoked'
            card.save()
        urlopen.assert_not_called()
