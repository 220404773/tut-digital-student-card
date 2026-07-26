"""Temporary diagnostic: confirm Google Wallet env vars load correctly in
production and a real save URL builds for an existing card. Not meant to
stay in the codebase - remove after use."""
from django.core.management.base import BaseCommand
from django.test import RequestFactory
from cards.models import StudentCard
from cards.utils import build_card_verification_url
from cards.google_wallet import build_google_wallet_save_url


class Command(BaseCommand):
    def handle(self, *args, **options):
        card = StudentCard.objects.first()
        if not card:
            self.stdout.write("No StudentCard rows exist to test against.")
            return

        request = RequestFactory().get(
            '/', SERVER_NAME='tut-digital-student-card-ivbr7.ondigitalocean.app', secure=True
        )
        verification_url = build_card_verification_url(card.card_number, request)
        save_url = build_google_wallet_save_url(card, verification_url)
        if save_url:
            self.stdout.write(f"OK - save_url built, length={len(save_url)}")
            self.stdout.write(save_url[:80] + "...")
        else:
            self.stdout.write("build_google_wallet_save_url returned None - credentials/issuer ID not loading")
