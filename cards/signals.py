from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import StudentCard


@receiver(post_delete, sender=StudentCard)
def delete_qr_code_on_card_delete(sender, instance, **kwargs):
    """Deleting a StudentCard row doesn't delete its qr_code file from
    storage on its own - Django never does that automatically for a
    FileField, regardless of backend. Without this, every deleted card
    (including ones cascade-deleted when a StudentUser is removed) leaves
    an orphaned QR image sitting in Spaces forever.
    """
    if instance.qr_code:
        instance.qr_code.storage.delete(instance.qr_code.name)
