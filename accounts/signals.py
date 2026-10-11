from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import User


@receiver(post_delete, sender=User)
def delete_user_files_on_delete(sender, instance, **kwargs):
    """Deleting a User doesn't delete their files from storage - the same
    Django limitation FileField has everywhere, S3/Spaces included. That
    includes a new photo still waiting for staff approval. Deleted once the
    delete commits, so a delete that fails keeps the student's photos.
    """
    for field in (instance.profile_picture, instance.pending_profile_picture, instance.registration_proof):
        if field:
            storage, name = field.storage, field.name
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))
