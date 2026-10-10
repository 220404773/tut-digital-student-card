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
)

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
