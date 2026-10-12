"""Every staff page must render with real data in it. Renaming StudentUser to
User changed Django's admin URL names (accounts_studentuser_* became
accounts_user_*), and the pages still pointing at the old names only crashed
once there was a card or student to link to - so empty-database tests missed it.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import DeletedStudentLog, User
from cards.models import CardActivity, OCRMetrics, PageVisit, SignupMetrics, StudentCard


class StaffPagesRenderWithDataTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
            first_name='Kekeletso', last_name='Khaka', is_verified=True, email_confirmed=True,
        )
        now = timezone.now()
        self.card = StudentCard.objects.create(
            user=self.student, card_number='TUT-TEST-0001', expiry_date=now.date() - timedelta(days=1),
        )
        CardActivity.objects.create(student_card=self.card, activity_type='viewed', ip_address='203.0.113.5')
        OCRMetrics.objects.create(
            user=self.student, ocr_start_time=now, ocr_end_time=now, ocr_duration_seconds=1.0,
            ocr_confidence_score=95, is_accurate=True,
        )
        SignupMetrics.objects.create(
            user=self.student, signup_start_time=now, signup_end_time=now, signup_duration_seconds=10,
        )
        PageVisit.objects.create(user=self.student, path='/cards/dashboard/', ip_address='203.0.113.5')
        self.deleted = DeletedStudentLog.objects.create(
            student_number='220404999', full_name='Former Student', email='220404999@tut4life.ac.za',
            was_verified=True, had_card=True, deleted_by=self.staff,
        )
        self.client.force_login(self.staff)

    def test_every_staff_report_renders(self):
        for name in ('performance_dashboard', 'report_cards', 'report_signups', 'report_email_confirmation',
                     'report_ocr', 'report_card_activity', 'report_deleted_students', 'report_expired_cards',
                     'report_visits', 'report_visit_users', 'staff_student_list'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_per_student_staff_pages_render(self):
        self.assertEqual(self.client.get(reverse('staff_student_detail', args=[self.student.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse('report_user_visits', args=[self.student.id])).status_code, 200)

    def test_student_detail_links_to_the_current_admin_pages(self):
        response = self.client.get(reverse('staff_student_detail', args=[self.student.id]))
        self.assertContains(response, reverse('admin:accounts_user_change', args=[self.student.id]))
        self.assertContains(response, reverse('admin:accounts_user_delete', args=[self.student.id]))

    def test_admin_change_page_shows_revoke_and_reissue_buttons(self):
        response = self.client.get(reverse('admin:accounts_user_change', args=[self.student.id]))
        self.assertContains(response, 'Revoke Card')
        self.assertContains(response, 'Reissue Card')

    def test_admin_change_page_has_no_form_inside_a_form(self):
        """Browsers drop a <form> nested in another, so the Revoke and Reissue
        buttons used to just save the student. The tests posted straight to
        their URLs and never noticed - this reads the page as a browser would."""
        from html.parser import HTMLParser

        class FormNesting(HTMLParser):
            depth = deepest = 0

            def handle_starttag(self, tag, attrs):
                if tag == 'form':
                    self.depth += 1
                    self.deepest = max(self.deepest, self.depth)

            def handle_endtag(self, tag):
                if tag == 'form':
                    self.depth -= 1

        parser = FormNesting()
        parser.feed(self.client.get(reverse('admin:accounts_user_change', args=[self.student.id])).content.decode())
        self.assertEqual(parser.deepest, 1)

    def test_admin_buttons_post_to_revoke_and_reissue_with_a_confirm_prompt(self):
        response = self.client.get(reverse('admin:accounts_user_change', args=[self.student.id]))
        for name in ('admin:accounts_user_revoke_card', 'admin:accounts_user_reissue_card'):
            with self.subTest(button=name):
                self.assertRegex(
                    response.content.decode(),
                    rf'<button type="submit"[^>]*formaction="{reverse(name, args=[self.student.id])}"[^>]*data-confirm=',
                )

    def test_revoking_with_the_whole_admin_form_posted_still_revokes(self):
        """What the browser now sends: the admin form's fields, to the revoke address."""
        self.card.status = 'active'
        self.card.save()
        self.client.post(reverse('admin:accounts_user_revoke_card', args=[self.student.id]),
                         {'email': self.student.email, 'first_name': 'Changed', '_save': 'Save'})
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'revoked')
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.first_name, 'Changed')

    def test_revoke_from_admin_returns_to_the_change_page(self):
        self.card.status = 'active'
        self.card.save()
        response = self.client.post(reverse('admin:accounts_user_revoke_card', args=[self.student.id]))
        self.assertRedirects(response, reverse('admin:accounts_user_change', args=[self.student.id]))
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'revoked')

    def test_pending_reviews_tile_counts_photo_and_name_changes_too(self):
        """One photo change and nothing else waiting used to read "0"."""
        self.student.pending_profile_picture = 'profile_pics/new.png'
        self.student.save()
        response = self.client.get(reverse('performance_dashboard'))
        self.assertEqual(response.context['card_stats']['pending_total_count'], 1)
        self.assertRegex(response.content.decode(), r'Pending Reviews</h6>\s*<h3>1</h3>')
        self.assertContains(response, '1 photo change')
        self.assertNotContains(response, 'registration review')
