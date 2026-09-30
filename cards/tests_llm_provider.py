"""LLM_PROVIDER decides where document text goes (POPIA s72). 'local' and
'none' must never reach NVIDIA, even when an NVIDIA key is configured.
"""
import io
import json
from unittest.mock import patch
from urllib.error import URLError

from django.test import SimpleTestCase, override_settings

from cards import utils
from cards.utils import LocalLLM, extract_with_llm, get_llm_client


def ollama_reply(content):
    """A fake HTTP response shaped like Ollama's /api/chat answer."""
    return io.BytesIO(json.dumps({'message': {'role': 'assistant', 'content': content}}).encode('utf-8'))


@override_settings(NVIDIA_API_KEY='nvapi-configured-but-must-not-be-used')
class ProviderSelectionTests(SimpleTestCase):
    @override_settings(LLM_PROVIDER='none')
    def test_none_uses_no_model(self):
        self.assertIsNone(get_llm_client())

    @override_settings(LLM_PROVIDER='local', LLM_BASE_URL='http://ollama:11434', LLM_MODEL='qwen2.5:3b')
    def test_local_returns_the_self_hosted_model(self):
        client = get_llm_client()
        self.assertIsInstance(client, LocalLLM)
        self.assertEqual(client.url, 'http://ollama:11434/api/chat')
        self.assertEqual(client.model, 'qwen2.5:3b')

    @override_settings(LLM_PROVIDER='local')
    def test_local_never_builds_the_nvidia_client(self):
        with patch.object(utils, 'ChatNVIDIA') as nvidia, patch.object(utils, 'LLM_AVAILABLE', True):
            get_llm_client()
        nvidia.assert_not_called()

    @override_settings(LLM_PROVIDER='none')
    def test_none_never_builds_the_nvidia_client(self):
        with patch.object(utils, 'ChatNVIDIA') as nvidia, patch.object(utils, 'LLM_AVAILABLE', True):
            get_llm_client()
        nvidia.assert_not_called()

    @override_settings(LLM_PROVIDER='nvidai')
    def test_a_misspelt_provider_is_refused_rather_than_treated_as_nvidia(self):
        with self.assertRaises(ValueError):
            get_llm_client()


class LocalLLMTests(SimpleTestCase):
    def test_sends_the_prompt_to_ollama_and_returns_its_answer(self):
        llm = LocalLLM('http://ollama:11434/', 'qwen2.5:3b', timeout=60)
        with patch('urllib.request.urlopen', return_value=ollama_reply('{"status": "ok"}')) as urlopen:
            response = llm.invoke('hello', response_format={'type': 'json_object'})

        request = urlopen.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://ollama:11434/api/chat')
        self.assertEqual(sent['model'], 'qwen2.5:3b')
        self.assertEqual(sent['format'], 'json')
        self.assertEqual(sent['messages'][0]['content'], 'hello')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 60)
        self.assertEqual(response.content, '{"status": "ok"}')

    @override_settings(LLM_PROVIDER='local')
    def test_extraction_uses_the_local_answer(self):
        answer = json.dumps({'is_tut_proof_of_registration': True, 'student_number': '220404773',
                             'full_name': 'Kekeletso Khaka', 'period': '01-Jan-2026 - 31-Dec-2026'})
        with patch('urllib.request.urlopen', return_value=ollama_reply(answer)):
            result = extract_with_llm('some OCR text')
        self.assertEqual(result['student_number'], '220404773')
        self.assertEqual(result['full_name'], 'Kekeletso Khaka')

    @override_settings(LLM_PROVIDER='local')
    def test_a_local_model_that_is_down_falls_back_instead_of_failing_the_signup(self):
        with patch('urllib.request.urlopen', side_effect=URLError('connection refused')):
            self.assertIsNone(extract_with_llm('some OCR text'))
