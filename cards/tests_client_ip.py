"""Behind a load balancer every request arrives from the proxy's address, so
the client IP has to come from a header the proxy sets - and only the part of
it the proxy wrote, since clients can send the same headers themselves.
"""
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

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


@override_settings(TRUSTED_PROXY_COUNT=1)
class LoginRateLimitTests(TestCase):
    """A whole campus reaches the site from one public IP address, so login
    is limited per account rather than per network: a class logging in
    together isn't locked out, but guessing one student's password still is."""

    def setUp(self):
        cache.clear()

    def _login_attempt(self, email, client_ip):
        return self.client.post(
            reverse('login'), {'email': email, 'password': 'wrong'},
            REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=client_ip,
        )

    def test_a_class_on_one_network_is_not_locked_out(self):
        """The old per-IP limit blocked the 11th student on campus Wi-Fi."""
        for number in range(15):
            response = self._login_attempt(f'2204047{number:02d}@tut4life.ac.za', '198.51.100.7')
            self.assertNotContains(response, 'Too many')

    def test_guessing_one_accounts_password_is_blocked_from_any_network(self):
        for _ in range(11):
            self._login_attempt('target@tut4life.ac.za', '198.51.100.7')
        self.assertContains(self._login_attempt('target@tut4life.ac.za', '198.51.100.7'), 'Too many')
        # Switching network doesn't reset the count for that account.
        self.assertContains(self._login_attempt('target@tut4life.ac.za', '203.0.113.9'), 'Too many')

    def test_email_case_does_not_create_a_separate_allowance(self):
        for _ in range(11):
            self._login_attempt('target@tut4life.ac.za', '198.51.100.7')
        self.assertContains(self._login_attempt('TARGET@tut4life.ac.za', '198.51.100.7'), 'Too many')
