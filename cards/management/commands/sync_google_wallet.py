"""Bring Google Wallet up to date with the app: the pass type's layout, and
every pass a student has saved (colour, logo, name, status). Run after each
deploy; it does nothing when Google Wallet isn't configured, and a Wallet
problem is reported without failing the deploy."""
from django.core.management.base import BaseCommand

from cards.google_wallet import _load_credentials, sync_wallet_pass, update_wallet_class
from cards.models import StudentCard


class Command(BaseCommand):
    help = "Update the Google Wallet pass type and every saved pass to match the app."

    def handle(self, *args, **options):
        if not _load_credentials():
            self.stdout.write("Google Wallet is not configured - nothing to do.")
            return
        try:
            update_wallet_class()
        except Exception as error:
            self.stderr.write(f"Google Wallet pass type not updated: {type(error).__name__} {error}")
            return
        cards = StudentCard.objects.select_related('user')
        updated = sum(1 for card in cards if sync_wallet_pass(card))
        self.stdout.write(f"Google Wallet pass type updated; {updated} saved pass(es) updated.")
