"""Shows where document text would be sent, and proves the configured model
actually answers - run it after deploying, before trusting a signup to it.

    python manage.py check_llm
"""
import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cards.utils import get_llm_client


class Command(BaseCommand):
    help = 'Report the configured LLM provider and send it one test request.'

    def handle(self, *args, **options):
        provider = settings.LLM_PROVIDER

        if provider == 'none':
            self.stdout.write(self.style.SUCCESS(
                'LLM_PROVIDER=none: no language model is used. Extraction is rule-based '
                'and document text never leaves this server.'
            ))
            return

        if provider == 'local':
            self.stdout.write(
                f'LLM_PROVIDER=local: document text goes to {settings.LLM_BASE_URL} '
                f'(model {settings.LLM_MODEL}). It stays in South Africa only if that machine is in South Africa.'
            )
        else:
            self.stdout.write(self.style.WARNING(
                'LLM_PROVIDER=nvidia: document text goes to NVIDIA\'s hosted API outside South Africa '
                '- a cross-border transfer under POPIA section 72.'
            ))

        llm = get_llm_client()
        if llm is None:
            raise CommandError('No model is available (for nvidia: missing library or NVIDIA_API_KEY). '
                               'Signups will fall back to rule-based extraction.')

        started = time.monotonic()
        try:
            response = llm.invoke('Reply with this JSON only: {"status": "ok"}',
                                  response_format={'type': 'json_object'})
            reply = json.loads(response.content)
        except Exception as e:
            raise CommandError(f'The model did not answer correctly: {e}')
        elapsed = time.monotonic() - started

        if reply.get('status') != 'ok':
            raise CommandError(f'The model answered, but not as instructed: {reply!r}')

        self.stdout.write(self.style.SUCCESS(f'Model answered correctly in {elapsed:.1f}s.'))
        if provider == 'local' and elapsed > settings.LLM_TIMEOUT_SECONDS / 2:
            self.stdout.write(self.style.WARNING(
                f'That is over half of LLM_TIMEOUT_SECONDS ({settings.LLM_TIMEOUT_SECONDS}s). A full document '
                f'is a much longer prompt, so real signups may time out and fall back to rule-based extraction.'
            ))
