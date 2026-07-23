"""
Temporary command to reset a specific account's email_confirmed flag back
to False for testing real email delivery. Not meant to stay in the
codebase - remove after use.
"""
from django.core.management.base import BaseCommand
from accounts.models import StudentUser


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('email', type=str)

    def handle(self, *args, **options):
        user = StudentUser.objects.get(email__iexact=options['email'])
        user.email_confirmed = False
        user.save()
        self.stdout.write(f"reset email_confirmed=False for {user.email}")
