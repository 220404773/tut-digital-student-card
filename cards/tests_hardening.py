"""Fixes from the round-5 review: pages that showed student details without a
login, the admin's own unguarded sign-in, the verify page for bad codes,
restoring a deleted student, files left behind, the visit log, the daily
retention clean-up and the size limits on documents read before login.
"""
import io
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import DeletedStudentLog, User
from cards.models import PageVisit, StudentCard
from cards.utils import MAX_PROOF_PIXELS, process_registration_proof

MEDIA = tempfile.mkdtemp()


def make_student(number='220404773', **extra):
    return User.objects.create_user(
        username=f'{number}@tut4life.ac.za', email=f'{number}@tut4life.ac.za', student_number=number,
        password='StrongPass123!', first_name='Kekeletso', last_name='Khaka', is_verified=True,
        email_confirmed=True, **extra,
    )


def make_staff():
    return User.objects.create_superuser(
        username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
        student_number='STAFF-0001', password='StrongPass123!',
    )


def png(size=(4, 4)):
    buf = io.BytesIO()
    Image.new('L', size).save(buf, format='PNG')
    return SimpleUploadedFile('me.png', buf.getvalue(), content_type='image/png')


class StudentSearchIsStaffOnlyTests(TestCase):
    """The email is the student number @tut4life.ac.za, so the old search let
    anyone - signed in or not - look up any student's details."""

    def setUp(self):
        self.student = make_student()

    def test_anonymous_visitors_cannot_search_or_open_a_student(self):
        for url in (reverse('user_search') + '?q=2204', reverse('student_detail', args=[self.student.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertNotContains(response, self.student.email, status_code=302)

    def test_a_signed_in_student_cannot_search_other_students(self):
        make_student('220404999')
        self.client.force_login(self.student)
        response = self.client.get(reverse('user_search') + '?q=220404999')
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('220404999', response.content.decode())

    def test_staff_are_sent_to_the_staff_directory(self):
        self.client.force_login(make_staff())
        response = self.client.get(reverse('user_search') + '?q=Khaka')
        self.assertRedirects(response, reverse('staff_student_list') + '?q=Khaka')
        response = self.client.get(reverse('student_detail', args=[self.student.pk]))
        self.assertRedirects(response, reverse('staff_student_detail', args=[self.student.pk]))

    def test_the_dashboard_no_longer_offers_find_students(self):
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Find Students')


class AdminSignInTests(TestCase):
    def test_the_admin_sign_in_goes_to_the_guarded_login_page(self):
        response = self.client.get('/admin/login/')
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_a_password_posted_to_the_admin_sign_in_signs_nobody_in(self):
        make_staff()
        self.client.post('/admin/login/', {'username': 'admin@tut4life.ac.za', 'password': 'StrongPass123!'})
        self.assertNotIn('_auth_user_id', self.client.session)


class LiveCardStylesTests(TestCase):
    def test_every_page_loads_the_live_card_styles(self):
        # The file name carries a content hash: css/style.<hash>.css
        self.assertRegex(self.client.get(reverse('home')).content.decode(), r'/static/css/style\.[0-9a-f]+\.css')


class VerifyPageForBadCodesTests(TestCase):
    def setUp(self):
        self.student = make_student()
        self.card = StudentCard.objects.create(
            user=self.student, card_number='TUT-HARD-0001', expiry_date=timezone.now().date() + timedelta(days=60),
        )

    def _verify(self, status):
        with patch('cards.views.live_code_status', return_value=status):
            return self.client.get(reverse('verify_card_public', args=[self.card.card_number]) + '?live=x')

    def test_a_code_that_is_not_genuine_shows_no_student_details(self):
        response = self._verify('invalid')
        self.assertContains(response, 'NOT A GENUINE CODE')
        self.assertNotContains(response, 'Kekeletso')
        self.assertNotContains(response, self.student.student_number)

    def test_an_old_code_shows_the_card_greyed_out(self):
        response = self._verify('expired')
        self.assertContains(response, 'OLD CODE')
        self.assertContains(response, 'card-superseded')

    def test_a_live_code_shows_the_card_normally(self):
        response = self._verify('live')
        self.assertContains(response, 'Kekeletso')
        self.assertNotContains(response, 'card-superseded')


class RestoreDeletedStudentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_staff()
        self.entry = DeletedStudentLog.objects.create(
            student_number='220404999', full_name='Former Student', email='220404999@tut4life.ac.za',
            was_verified=True, had_card=True, deleted_by=self.staff,
        )
        self.client.force_login(self.staff)
        self.url = reverse('reissue_deleted_student', args=[self.entry.pk])

    def test_a_double_click_restores_once_and_does_not_crash(self):
        self.client.post(self.url)
        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(student_number='220404999').count(), 1)

    def test_a_failed_email_still_marks_the_entry_restored(self):
        with patch('cards.views.send_confirmation_email', side_effect=RuntimeError('mail down')):
            response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'the emails could not be sent')
        self.entry.refresh_from_db()
        self.assertIsNotNone(self.entry.restored_at)
        self.assertTrue(User.objects.filter(student_number='220404999').exists())


@override_settings(MEDIA_ROOT=MEDIA)
class DeletedStudentFilesTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_deleting_a_student_deletes_a_photo_waiting_for_approval(self):
        student = make_student(profile_picture=png(), pending_profile_picture=png())
        photos = (student.profile_picture.name, student.pending_profile_picture.name)
        with self.captureOnCommitCallbacks(execute=True):
            student.delete()
        for name in photos:
            self.assertFalse(default_storage.exists(name), name)


class VisitLogTests(TestCase):
    def test_the_live_qr_refresh_and_keepalive_are_not_counted_as_visits(self):
        student = make_student()
        StudentCard.objects.create(user=student, card_number='TUT-HARD-0002',
                                   expiry_date=timezone.now().date() + timedelta(days=60))
        self.client.force_login(student)
        self.client.get(reverse('card_qr_image') + '?live=1')
        self.client.get(reverse('session_keepalive'))
        self.assertFalse(PageVisit.objects.exists())
        self.client.get(reverse('dashboard'))
        self.assertTrue(PageVisit.objects.filter(path=reverse('dashboard')).exists())


@override_settings(ACTIVITY_RETENTION_DAYS=30)
class DailyCleanUpTests(TestCase):
    def setUp(self):
        cache.clear()

    def _old_visit(self):
        visit = PageVisit.objects.create(path='/old/', ip_address='203.0.113.5')
        PageVisit.objects.filter(pk=visit.pk).update(timestamp=timezone.now() - timedelta(days=60))
        return visit

    def test_the_first_request_of_the_day_removes_old_visits(self):
        visit = self._old_visit()
        self.client.get(reverse('home'))
        self.assertFalse(PageVisit.objects.filter(pk=visit.pk).exists())

    def test_it_runs_only_once_a_day(self):
        self.client.get(reverse('home'))
        visit = self._old_visit()
        self.client.get(reverse('home'))
        self.assertTrue(PageVisit.objects.filter(pk=visit.pk).exists())


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentSizeLimitTests(TestCase):
    def test_an_image_declaring_too_many_pixels_is_not_read(self):
        side = int(MAX_PROOF_PIXELS ** 0.5) + 100
        buf = io.BytesIO()
        Image.new('L', (side, side)).save(buf, format='PNG')
        upload = SimpleUploadedFile('proof.png', buf.getvalue(), content_type='image/png')
        with patch('cards.utils.OCR_AVAILABLE', True), \
                patch('cards.utils.ocr_image_with_confidence') as ocr:
            self.assertIsNone(process_registration_proof(upload))
        ocr.assert_not_called()

    def test_only_the_first_pages_of_a_long_pdf_are_read(self):
        import fitz

        from cards.utils import extract_pdf_text

        document = fitz.open()
        for number in range(20):
            document.new_page().insert_text((72, 72), f'page {number}')
        path = f'{MEDIA}/long.pdf'
        document.save(path)
        text = extract_pdf_text(path)
        self.assertIn('page 2', text)
        self.assertNotIn('page 3', text)


class ReuploadAfterRevokeTests(TestCase):
    """Reading the document takes seconds; a card revoked by staff in that
    time must not be switched back on by the upload."""

    def test_a_card_revoked_while_the_document_is_read_stays_revoked(self):
        from accounts.tests_identity import ocr_result, proof

        cache.clear()
        student = make_student()
        card = StudentCard.objects.create(user=student, card_number='TUT-HARD-0003', status='expired',
                                          expiry_date=timezone.now().date() - timedelta(days=1))

        def revoke_meanwhile(_file):
            StudentCard.objects.filter(pk=card.pk).update(status='revoked')
            return ocr_result('220404773')

        self.client.force_login(student)
        with patch('accounts.views.process_registration_proof', side_effect=revoke_meanwhile):
            response = self.client.post(reverse('reupload_proof'), {'registration_proof': proof()}, follow=True)
        self.assertContains(response, 'revoked by an administrator')
        card.refresh_from_db()
        self.assertEqual(card.status, 'revoked')


class RestoredStudentLinksInAStaffBrowserTests(TestCase):
    """Staff restore a student, then the student's emails are opened in the
    same browser: the student must not end up in the staff pages."""

    def setUp(self):
        from django.core import mail

        cache.clear()
        self.staff = make_staff()
        entry = DeletedStudentLog.objects.create(
            student_number='220404999', full_name='Former Student', email='220404999@tut4life.ac.za',
            was_verified=True, had_card=True, deleted_by=self.staff,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse('reissue_deleted_student', args=[entry.pk]))
        self.student = User.objects.get(student_number='220404999')
        self.emails = [message.body for message in mail.outbox]

    def _link(self, pattern):
        import re

        for body in self.emails:
            match = re.search(pattern, body)
            if match:
                return match.group(1)
        self.fail(f'No email with a link matching {pattern}')

    def test_the_restored_account_is_not_staff(self):
        self.assertFalse(self.student.is_staff)
        self.assertFalse(self.student.is_superuser)

    def test_confirming_the_email_signs_the_staff_member_out(self):
        response = self.client.post(self._link(r'(/accounts/confirm-email/\S+/)'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.student.refresh_from_db()
        self.assertTrue(self.student.email_confirmed)

    def test_setting_a_new_password_signs_the_staff_member_out(self):
        link = self._link(r'(/accounts/password-reset-confirm/\S+/)')
        form_page = self.client.get(link, follow=True)  # Django swaps the token for a session marker
        response = self.client.post(form_page.request['PATH_INFO'], {
            'new_password1': 'AnotherStrong123!', 'new_password2': 'AnotherStrong123!',
        })
        self.assertRedirects(response, reverse('password_reset_complete'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)  # the login form, not the staff dashboard

    def test_the_student_signed_in_on_their_own_device_stays_signed_in(self):
        self.client.logout()
        self.client.force_login(self.student)
        self.client.post(self._link(r'(/accounts/confirm-email/\S+/)'))
        self.assertEqual(self.client.session['_auth_user_id'], str(self.student.pk))
