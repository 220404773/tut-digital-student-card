"""
Temporary command used only to verify the S3/Spaces storage fix on the live
deployment: marks a specific test user verified, generates their student
card + QR code through the normal code path, and prints the resulting QR
URL so it can be checked against the Spaces bucket. Not meant to stay in
the codebase - remove after the deployment verification is done.
"""
from django.core.management.base import BaseCommand
from accounts.models import StudentUser
from cards.utils import generate_student_card


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('user_id', type=int)

    def handle(self, *args, **options):
        user = StudentUser.objects.get(pk=options['user_id'])
        user.is_verified = True
        user.save()
        card = generate_student_card(user, request=None)
        self.stdout.write(f"card_number={card.card_number}")
        self.stdout.write(f"qr_code_name={card.qr_code.name}")
        self.stdout.write(f"qr_code_url={card.qr_code.url}")
