"""Temporary command to inspect one account's verification status before
deciding whether to delete its leftover registration proof file."""
from django.core.management.base import BaseCommand
from accounts.models import StudentUser


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('email', type=str)

    def handle(self, *args, **options):
        user = StudentUser.objects.get(email__iexact=options['email'])
        self.stdout.write(f"is_verified={user.is_verified}")
        self.stdout.write(f"email_confirmed={user.email_confirmed}")
        self.stdout.write(f"registration_proof={user.registration_proof}")
        self.stdout.write(f"student_number={user.student_number}")
        self.stdout.write(f"ocr_student_number={user.ocr_student_number}")
        self.stdout.write(f"ocr_full_name={user.ocr_full_name}")
