"""Document text goes to our Azure OpenAI deployment, with ID numbers removed
first - the system never uses them, so they have no reason to leave the server.
"""
import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from cards.utils import AzureOpenAIClient, extract_with_llm, get_llm_client, redact_id_numbers

CONFIGURED = dict(
    AZURE_OPENAI_ENDPOINT='https://example-resource.openai.azure.com/',
    AZURE_OPENAI_API_KEY='test-key',
    AZURE_OPENAI_DEPLOYMENT='gpt-5-nano',
    AZURE_OPENAI_API_VERSION='2025-04-01-preview',
    AZURE_OPENAI_REASONING_EFFORT='minimal',
    # Pinned so these tests don't depend on the keys in the developer's .env.
    NVIDIA_API_KEY='',
)
WITH_FALLBACK = dict(CONFIGURED, NVIDIA_API_KEY='nvapi-test', NVIDIA_MODEL='nvidia/test-model')

DOCUMENT_TEXT = (
    'TSHWANE UNIVERSITY OF TECHNOLOGY\n'
    'Student Number: 220404773\n'
    'Name: Kekeletso Khaka\n'
    'ID Number: 0001015800085\n'
    'Registered from 03-Jan-2026 to 31-Dec-2026\n'
)


def azure_reply(content):
    """A fake HTTP response shaped like Azure OpenAI's chat completion."""
    return io.BytesIO(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': content}}]}).encode())


class RedactIdNumbersTests(SimpleTestCase):
    def test_removes_a_13_digit_id_number(self):
        self.assertNotIn('0001015800085', redact_id_numbers('ID 0001015800085 issued'))

    def test_removes_an_id_number_printed_in_groups(self):
        self.assertNotIn('5800', redact_id_numbers('Identity: 000101 5800 085'))

    def test_removes_a_shorter_number_when_labelled_as_an_id_number(self):
        self.assertEqual(redact_id_numbers('ID Number: 1234567890'), 'ID Number: [REMOVED]')

    def test_keeps_the_student_number_and_other_numbers(self):
        """The 9-digit student number is exactly what the model must read."""
        text = 'Student Number: 220404773, phone 0123456789, year 2026'
        self.assertEqual(redact_id_numbers(text), text)


class AzureClientTests(SimpleTestCase):
    @override_settings(AZURE_OPENAI_ENDPOINT='', AZURE_OPENAI_API_KEY='', AZURE_OPENAI_DEPLOYMENT='')
    def test_unconfigured_means_no_model_and_nothing_sent(self):
        self.assertIsNone(get_llm_client())

    @override_settings(**CONFIGURED)
    def test_calls_the_named_deployment_with_the_key(self):
        with patch('urllib.request.urlopen', return_value=azure_reply('{"status": "ok"}')) as urlopen:
            response = get_llm_client().invoke('Reply in JSON', response_format={'type': 'json_object'})

        request = urlopen.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(
            request.full_url,
            'https://example-resource.openai.azure.com/openai/deployments/gpt-5-nano'
            '/chat/completions?api-version=2025-04-01-preview',
        )
        self.assertEqual(request.get_header('Api-key'), 'test-key')
        self.assertEqual(sent['response_format'], {'type': 'json_object'})
        self.assertEqual(sent['reasoning_effort'], 'minimal')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 20)
        self.assertEqual(response.content, '{"status": "ok"}')

    def test_blank_reasoning_effort_is_left_out_for_models_that_reject_it(self):
        client = AzureOpenAIClient('https://x.openai.azure.com', 'k', 'gpt-4o-mini', 'v', '', 20)
        with patch('urllib.request.urlopen', return_value=azure_reply('{}')) as urlopen:
            client.invoke('JSON please')
        self.assertNotIn('reasoning_effort', json.loads(urlopen.call_args.args[0].data))

    def test_an_azure_error_reports_what_azure_objected_to(self):
        client = AzureOpenAIClient('https://x.openai.azure.com', 'k', 'gpt-5-nano', 'v', 'minimal', 20)
        error = HTTPError('url', 400, 'Bad Request', {}, io.BytesIO(b'{"error": "Unsupported parameter"}'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaisesMessage(RuntimeError, 'Unsupported parameter'):
                client.invoke('JSON please')


@override_settings(**CONFIGURED)
class ExtractWithAzureTests(SimpleTestCase):
    def test_the_id_number_never_reaches_azure(self):
        answer = json.dumps({'is_tut_proof_of_registration': True, 'student_number': '220404773'})
        with patch('urllib.request.urlopen', return_value=azure_reply(answer)) as urlopen:
            extract_with_llm(DOCUMENT_TEXT)

        prompt = json.loads(urlopen.call_args.args[0].data)['messages'][0]['content']
        self.assertNotIn('0001015800085', prompt)
        self.assertNotIn('id_number', prompt)
        self.assertIn('220404773', prompt)

    def test_extraction_uses_the_model_answer(self):
        answer = json.dumps({'is_tut_proof_of_registration': True, 'student_number': '220404773',
                             'full_name': 'Kekeletso Khaka', 'period': '03-Jan-2026 to 31-Dec-2026'})
        with patch('urllib.request.urlopen', return_value=azure_reply(answer)):
            result = extract_with_llm(DOCUMENT_TEXT)
        self.assertEqual(result['student_number'], '220404773')
        self.assertEqual(result['full_name'], 'Kekeletso Khaka')

    def test_timeout_falls_back_instead_of_failing_the_signup(self):
        """ET-02: a model that doesn't answer in time must not block registration."""
        with patch('urllib.request.urlopen', side_effect=URLError('timed out')):
            self.assertIsNone(extract_with_llm(DOCUMENT_TEXT))


def nvidia_reply(content):
    return io.BytesIO(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': content}}]}).encode())


@override_settings(**WITH_FALLBACK)
class NvidiaFallbackTests(SimpleTestCase):
    """NVIDIA has no data processing agreement with us, so it is only ever
    asked when Azure has already failed."""

    ANSWER = json.dumps({'is_tut_proof_of_registration': True, 'student_number': '220404773'})

    def _requests_by_host(self, urlopen):
        return [call.args[0] for call in urlopen.call_args_list]

    def test_nvidia_is_not_called_while_azure_answers(self):
        with patch('urllib.request.urlopen', return_value=azure_reply(self.ANSWER)) as urlopen:
            extract_with_llm(DOCUMENT_TEXT)
        hosts = [r.host for r in self._requests_by_host(urlopen)]
        self.assertEqual(hosts, ['example-resource.openai.azure.com'])

    def test_nvidia_answers_when_azure_fails(self):
        def respond(request, timeout):
            if 'azure' in request.host:
                raise URLError('Azure unreachable')
            return nvidia_reply(self.ANSWER)

        with patch('urllib.request.urlopen', side_effect=respond) as urlopen:
            result = extract_with_llm(DOCUMENT_TEXT)

        self.assertEqual(result['student_number'], '220404773')
        nvidia_request = self._requests_by_host(urlopen)[1]
        self.assertEqual(nvidia_request.host, 'integrate.api.nvidia.com')
        self.assertEqual(json.loads(nvidia_request.data)['model'], 'nvidia/test-model')

    def test_the_id_number_is_removed_before_nvidia_too(self):
        def respond(request, timeout):
            if 'azure' in request.host:
                raise URLError('Azure unreachable')
            return nvidia_reply(self.ANSWER)

        with patch('urllib.request.urlopen', side_effect=respond) as urlopen:
            extract_with_llm(DOCUMENT_TEXT)
        prompt = json.loads(self._requests_by_host(urlopen)[1].data)['messages'][0]['content']
        self.assertNotIn('0001015800085', prompt)

    def test_both_failing_falls_back_to_rule_based_extraction(self):
        with patch('urllib.request.urlopen', side_effect=URLError('down')):
            self.assertIsNone(extract_with_llm(DOCUMENT_TEXT))

    def test_a_hanging_azure_still_leaves_the_fallback_inside_20_seconds(self):
        """ET-02: Azure is capped at 12 seconds; if it hangs that long,
        NVIDIA gets only what's left of the 20, not a fresh 20."""
        clock = [1000.0]
        timeouts = []

        def respond(request, timeout):
            timeouts.append(timeout)
            if 'azure' in request.host:
                clock[0] += timeout  # Azure hangs until its own timeout
                raise URLError('timed out')
            return nvidia_reply(self.ANSWER)

        with patch('cards.utils.time.monotonic', side_effect=lambda: clock[0]), \
                patch('urllib.request.urlopen', side_effect=respond):
            extract_with_llm(DOCUMENT_TEXT)
        self.assertEqual(timeouts[0], 12)
        self.assertAlmostEqual(timeouts[1], 8)

    def test_an_instantly_failing_azure_gives_the_fallback_the_full_budget(self):
        timeouts = []

        def respond(request, timeout):
            timeouts.append(timeout)
            if 'azure' in request.host:
                raise URLError('connection refused')
            return nvidia_reply(self.ANSWER)

        with patch('urllib.request.urlopen', side_effect=respond):
            extract_with_llm(DOCUMENT_TEXT)
        self.assertGreater(timeouts[1], 19)
