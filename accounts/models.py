from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Student user with personal and verification information."""
    
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other')
    ]
    
    # Student information
    student_number = models.CharField(max_length=20, unique=True)
    phone = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    
    # Registration document
    registration_proof = models.FileField(upload_to='registration_proofs/', null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True)

    # Confirms the student actually controls the email address on the
    # account - independent of is_verified, which only checks that an
    # uploaded document was read successfully (a document can be edited;
    # this checks the mailbox is real and reachable).
    email_confirmed = models.BooleanField(default=False)
    email_confirmed_at = models.DateTimeField(null=True, blank=True)

    # When the student accepted the Privacy Policy / Terms at signup. POPIA
    # requires the responsible party to be able to show consent was given, so
    # the checkbox alone isn't enough - the timestamp is the actual record.
    consent_given_at = models.DateTimeField(null=True, blank=True)

    # SHA-256 of the proof-of-registration file this account was verified
    # with. The file itself is deleted straight after processing (POPIA), so
    # this stores only the fingerprint - enough to notice the same file being
    # submitted for a second account, without retaining the document.
    registration_proof_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)

    registration_end_date = models.DateField(null=True, blank=True)
    
    class Meta:
        ordering = ['-date_joined']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    
    def get_full_name_display(self):
        return f"{self.first_name} {self.last_name}"


class DeletedStudentLog(models.Model):
    """Snapshot of a student's identifying info, captured at the moment
    they're deleted from Django admin. Deleting a User cascades and
    removes their StudentCard/CardActivity/metrics rows too - without this,
    there would be no record a student ever existed once that happens.
    Written explicitly by StudentUserAdmin.delete_model()/delete_queryset()
    (the only two places a student is ever deleted in this app), not by a
    signal, so "who deleted this" is captured reliably from the admin
    request rather than guessed at.
    """
    student_number = models.CharField(max_length=20)
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    was_verified = models.BooleanField()
    had_card = models.BooleanField()
    card_number = models.CharField(max_length=20, blank=True, default='')
    registration_end_date = models.DateField(null=True, blank=True)
    was_email_confirmed = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(auto_now_add=True)
    deleted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    # Set once staff recreate this student's account from this snapshot
    # (see cards/views.py:reissue_deleted_student) - guards the report from
    # offering "Reissue" twice for the same entry.
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f"{self.full_name} ({self.student_number}) - deleted {self.deleted_at}"
