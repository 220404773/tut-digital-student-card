from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import StudentUser


@receiver(post_delete, sender=StudentUser)
def delete_user_files_on_delete(sender, instance, **kwargs):
    """Deleting a StudentUser doesn't delete their profile picture (or a
    stray registration_proof, if one was ever left set) from storage -
    same Django limitation FileField has everywhere, S3/Spaces included.
    """
    if instance.profile_picture:
        instance.profile_picture.storage.delete(instance.profile_picture.name)
    if instance.registration_proof:
        instance.registration_proof.storage.delete(instance.registration_proof.name)
