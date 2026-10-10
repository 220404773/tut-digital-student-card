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
    # A new photo uploaded after the card was issued waits here until a staff
    # member approves it - the card's photo is what a guard checks the
    # holder's face against (settings.REQUIRE_PHOTO_FOR_CARD).
    pending_profile_picture = models.ImageField(upload_to='profile_pictures/pending/', null=True, blank=True)
    # Likewise a name change after the card was issued: the name is printed on
    # the card, so it only changes once a staff member approves it.
    pending_first_name = models.CharField(max_length=150, blank=True, default='')
    pending_last_name = models.CharField(max_length=150, blank=True, default='')
    name_change_requested_at = models.DateTimeField(null=True, blank=True)

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


class ReviewRequest(models.Model):
    """A request for a staff member to check a registration the system
    didn't verify automatically.

    POPIA section 71: a decision based solely on automated processing - here,
    whether someone is accepted as a registered student - must give the person
    a way to have it looked at by a human. Created automatically when the
    system itself is unsure (student-number mismatch, a borderline confidence
    score), or by the student when they disagree with a rejection. Staff
    resolve it by confirming registration through the university's own
    records, since the uploaded document is deleted after processing.
    """
    REASON_CHOICES = [
        ('number_mismatch', 'Student number on document differs from the one entered'),
        ('low_confidence', 'Document read with borderline confidence'),
        ('document_rejected', 'Document rejected as not a TUT proof of registration'),
        ('student_request', 'Student asked for a review'),
        ('suspicious_content', 'Document text appeared to give instructions to the AI'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_requests')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    message = models.TextField(blank=True, max_length=1000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    staff_note = models.TextField(blank=True, max_length=1000)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # At most one open request per student, enforced by the database
            # so two requests at the same moment can't both create one.
            models.UniqueConstraint(
                fields=['user'], condition=models.Q(status='pending'), name='one_pending_review_per_student',
            ),
        ]

    def __str__(self):
        return f"{self.user.student_number} - {self.get_reason_display()} ({self.status})"

    @classmethod
    def open_for(cls, user, reason, message=''):
        """Return the student's open request, creating one if there is none -
        a student shouldn't be able to queue up duplicates for staff."""
        from django.db import IntegrityError, transaction

        existing = cls.objects.filter(user=user, status='pending').first()
        if existing:
            return existing
        try:
            with transaction.atomic():
                return cls.objects.create(user=user, reason=reason, message=message)
        except IntegrityError:
            # A simultaneous request opened one first.
            return cls.objects.get(user=user, status='pending')
