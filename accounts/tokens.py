from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    """Separate from the password-reset token generator so a confirmation
    link can never double as a password-reset link, and so the token
    becomes invalid once the email has already been confirmed."""

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{timestamp}{user.email_confirmed}"


email_confirmation_token = EmailConfirmationTokenGenerator()
