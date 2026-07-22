"""
Temporary command to find a working (non-deprecated) NVIDIA chat model ID.
Not meant to stay in the codebase - remove after diagnosis is done.
"""
from django.core.management.base import BaseCommand
from cards.utils import get_nvidia_api_key
from langchain_nvidia_ai_endpoints import ChatNVIDIA


CANDIDATES = [
    'meta/llama-3.1-70b-instruct',
    'meta/llama-3.3-70b-instruct',
    'meta/llama-3.1-8b-instruct',
    'meta/llama3-70b-instruct',
]


class Command(BaseCommand):
    def handle(self, *args, **options):
        api_key = get_nvidia_api_key()
        self.stdout.write(f"api_key_present={bool(api_key)}")
        for model in CANDIDATES:
            try:
                llm = ChatNVIDIA(model=model, nvidia_api_key=api_key)
                resp = llm.invoke('Reply with JSON {"ok": true}', response_format={"type": "json_object"})
                self.stdout.write(f"MODEL_OK model={model} content={resp.content[:200]!r}")
            except Exception as e:
                self.stdout.write(f"MODEL_FAIL model={model} error={type(e).__name__}: {str(e)[:300]}")
