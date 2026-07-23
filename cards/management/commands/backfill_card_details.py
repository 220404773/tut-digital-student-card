"""
Temporary one-off command to backfill card_number/qr_code for any
existing StudentCard rows left incomplete from before the admin
auto-generation fix. Not meant to stay in the codebase - remove after use.
"""
from django.core.management.base import BaseCommand
from django.test import RequestFactory
from cards.models import StudentCard
from cards.utils import generate_unique_card_number, generate_qr_code_for_card


class Command(BaseCommand):
    def handle(self, *args, **options):
        # generate_qr_code_for_card needs a real request to build an
        # absolute URL (request=None would encode a useless relative
        # path into the QR image) - there's no live request in a
        # management command, so build one against the real domain.
        request = RequestFactory().get(
            '/', SERVER_NAME='tut-digital-student-card-ivbr7.ondigitalocean.app', secure=True
        )

        fixed = 0
        for card in StudentCard.objects.all():
            was_missing = not card.card_number or not card.qr_code
            if not card.card_number:
                card.card_number = generate_unique_card_number(card.user)
                card.save()
            if not card.qr_code:
                generate_qr_code_for_card(card, request=request)
            if was_missing:
                fixed += 1
                self.stdout.write(f"Fixed card id={card.id} user={card.user.email} card_number={card.card_number}")
        self.stdout.write(f"Done. Fixed {fixed} card(s).")
