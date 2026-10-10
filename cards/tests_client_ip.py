"""Behind a load balancer every request arrives from the proxy's address, so
the client IP has to come from a header the proxy sets - and only the part of
it the proxy wrote, since clients can send the same headers themselves.
"""
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from tut_student_card.client_ip import get_client_ip

PROXY = '10.0.0.1'


def request_with(**meta):
    return RequestFactory().get('/', REMOTE_ADDR=PROXY, **meta)


class GetClientIpTests(SimpleTestCase):
    def test_without_proxy_settings_the_connecting_address_is_used(self):
        self.assertEqual(get_client_ip(request_with(HTTP_X_FORWARDED_FOR='198.51.100.7')), PROXY)

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_reads_the_entry_our_proxy_appended(self):
        request = request_with(HTTP_X_FORWARDED_FOR='198.51.100.7')
        self.assertEqual(get_client_ip(request), '198.51.100.7')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_a_forged_leftmost_entry_is_ignored(self):
        """The client wrote '1.2.3.4'; our proxy appended the real address."""
        request = request_with(HTTP_X_FORWARDED_FOR='1.2.3.4, 198.51.100.7')
        self.assertEqual(get_client_ip(request), '198.51.100.7')

    @override_settings(CLIENT_IP_HEADER='DO-Connecting-IP')
    def test_a_proxy_set_header_is_preferred(self):
        request = request_with(HTTP_DO_CONNECTING_IP='198.51.100.9', HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(get_client_ip(request), '198.51.100.9')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_a_malformed_header_falls_back_instead_of_reaching_the_database(self):
        self.assertEqual(get_client_ip(request_with(HTTP_X_FORWARDED_FOR='not-an-ip')), PROXY)

    @override_settings(CLIENT_IP_HEADER='DO-Connecting-IP')
    def test_ipv6_clients_are_supported(self):
        request = request_with(HTTP_DO_CONNECTING_IP='2001:db8::1')
        self.assertEqual(get_client_ip(request), '2001:db8::1')


ATTACKER = '198.51.100.7'
STUDENT = '203.0.113.9'


@override_settings(TRUSTED_PROXY_COUNT=1)
class LoginProtectionTests(TestCase):
    """Only failed logins count, per email and network (accounts/login_guard.py):
    a class on one campus network isn't locked out, and someone typing junk
    at a student's predictable address can't lock that student out either."""

    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!',
        )

    def _login(self, email, client_ip, password='wrong'):
        return self.client.post(
            reverse('login'), {'email': email, 'password': password},
            REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=client_ip,
        )

    def test_a_class_on_one_network_is_not_locked_out(self):
        """The old per-IP limit blocked the 11th student on campus Wi-Fi."""
        for number in range(15):
            self.assertNotContains(self._login(f'2204047{number:02d}@tut4life.ac.za', ATTACKER), 'Too many')

    def test_repeated_failures_make_that_network_wait(self):
        for _ in range(5):
            self._login('220404773@tut4life.ac.za', ATTACKER)
        self.assertContains(self._login('220404773@tut4life.ac.za', ATTACKER), 'Please wait 30 seconds')

    def test_the_wait_doubles_with_each_further_failure(self):
        from accounts import login_guard
        for _ in range(6):
            login_guard.record_failed_login('220404773@tut4life.ac.za', ATTACKER)
        self.assertAlmostEqual(login_guard.login_wait_seconds('220404773@tut4life.ac.za', ATTACKER), 60, delta=1)

    def test_an_attacker_cannot_lock_the_student_out(self):
        """The finding: junk logins at a student's address used to block the
        real student too. Now they only slow down the attacker's network."""
        for _ in range(20):
            self._login('220404773@tut4life.ac.za', ATTACKER)
        response = self._login('220404773@tut4life.ac.za', STUDENT, password='StrongPass123!')
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_guessing_spread_over_many_networks_pauses_the_account(self):
        for number in range(50):
            self._login('220404773@tut4life.ac.za', f'192.0.2.{number}')
        self.assertContains(self._login('220404773@tut4life.ac.za', STUDENT, password='StrongPass123!'),
                            'Too many failed login attempts')

    def test_a_successful_login_clears_that_networks_count(self):
        for _ in range(4):
            self._login('220404773@tut4life.ac.za', STUDENT)
        self._login('220404773@tut4life.ac.za', STUDENT, password='StrongPass123!')
        self.client.logout()
        self._login('220404773@tut4life.ac.za', STUDENT)
        self.assertNotContains(self._login('220404773@tut4life.ac.za', STUDENT), 'Too many')

    def test_email_case_does_not_create_a_separate_allowance(self):
        for _ in range(5):
            self._login('220404773@tut4life.ac.za', ATTACKER)
        self.assertContains(self._login('220404773@TUT4LIFE.ac.za', ATTACKER), 'Too many')
