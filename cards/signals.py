"""Keeps a student's Google Wallet pass in line with their card
(cards/google_wallet.py). Every change to a saved card - revoked, reissued,
expired, reactivated, a new expiry date - goes through here, and the pass is
updated only once that change has been committed."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .google_wallet import deactivate_wallet_pass, sync_wallet_pass
from .models import StudentCard

_WATCHED = ('card_number', 'status', 'expiry_date')


@receiver(pre_save, sender=StudentCard)
def _remember_card_before_saving(sender, instance, **kwargs):
    before = StudentCard.objects.filter(pk=instance.pk).values(*_WATCHED).first() if instance.pk else None
    instance._wallet_before = before


@receiver(post_save, sender=StudentCard)
def _update_wallet_pass(sender, instance, created, **kwargs):
    before = getattr(instance, '_wallet_before', None)
    if created or not before:
        return  # a new card has no pass yet - the student adds it themselves
    if before['card_number'] != instance.card_number:
        # Reissued: the pass for the old number stops working; the student
        # adds the new card to Wallet again.
        old_number = before['card_number']
        transaction.on_commit(lambda: deactivate_wallet_pass(old_number))
    elif any(before[field] != getattr(instance, field) for field in ('status', 'expiry_date')):
        transaction.on_commit(lambda: sync_wallet_pass(instance))


@receiver(post_delete, sender=StudentCard)
def _deactivate_deleted_cards_pass(sender, instance, **kwargs):
    card_number = instance.card_number
    transaction.on_commit(lambda: deactivate_wallet_pass(card_number))


def sync_wallet_for_student(student):
    """For changes that don't save the card itself: a new name on the card,
    or cards updated in bulk (QuerySet.update skips the signals above)."""
    student_id = student.pk

    def sync():
        # Read once committed, so the pass gets the new name, not the old one.
        card = StudentCard.objects.filter(user_id=student_id).select_related('user').first()
        if card:
            sync_wallet_pass(card)
    transaction.on_commit(sync)
