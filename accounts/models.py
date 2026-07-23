from django.db import models
from django.contrib.auth.models import AbstractUser


class StudentUser(AbstractUser):
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

    # Auto-extracted from document
    ocr_student_number = models.CharField(max_length=20, null=True, blank=True)
    ocr_full_name = models.CharField(max_length=255, null=True, blank=True)
    registration_end_date = models.DateField(null=True, blank=True)
    
    class Meta:
        ordering = ['-date_joined']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    
    def get_full_name_display(self):
        return f"{self.first_name} {self.last_name}"
