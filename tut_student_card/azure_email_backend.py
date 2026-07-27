"""
Django email backend for Azure Communication Services Email.

Used instead of SMTP because Brevo (and the domain-verification
requirements common to SendGrid/Resend) couldn't properly authenticate a
sender on tut4life.ac.za - a domain we don't control the DNS for. Azure's
free Managed Domain comes with SPF/DKIM already configured by Microsoft,
which resolved the delivery problem (messages were being accepted then
silently quarantined by the recipient's mail security).
"""
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from azure.communication.email import EmailClient


class AzureCommunicationEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        client = EmailClient.from_connection_string(settings.AZURE_EMAIL_CONNECTION_STRING)
        sent_count = 0

        for message in email_messages:
            recipients = list(message.to) + list(message.cc or []) + list(message.bcc or [])
            azure_message = {
                "senderAddress": settings.AZURE_EMAIL_SENDER_ADDRESS,
                "recipients": {
                    "to": [{"address": address} for address in recipients],
                },
                "content": {
                    "subject": message.subject,
                    "plainText": message.body,
                },
            }
            try:
                # begin_send() itself already blocks until Azure accepts the
                # message for delivery (same guarantee an SMTP relay's "250
                # OK" gives - queued, not yet delivered). Deliberately not
                # calling poller.result(), which instead polls until Azure
                # finishes the entire send pipeline - that wait was adding
                # several extra seconds to every signup/password-reset
                # request for a completion status nothing here ever used.
                client.begin_send(azure_message)
                sent_count += 1
            except Exception:
                if not self.fail_silently:
                    raise

        return sent_count
