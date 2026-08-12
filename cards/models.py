from django.db import models
from django.utils import timezone
from accounts.models import StudentUser


class StudentCard(models.Model):
    """Digital card for a student. One card per student."""
    user = models.OneToOneField(StudentUser, on_delete=models.CASCADE, related_name='student_card')
    card_number = models.CharField(max_length=20, unique=True)
    qr_code = models.ImageField(upload_to='qr_codes/', null=True, blank=True)
    issue_date = models.DateTimeField(auto_now_add=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[('active', 'Active'), ('expired', 'Expired'), ('revoked', 'Revoked')],
        default='active',
    )
    
    def __str__(self):
        return f"Card {self.card_number}"

    def is_valid(self):
        """Check if card is active. Does NOT itself check expiry_date - call
        refresh_status() first wherever a card is actually read/displayed,
        so an overdue card has already been soft-expired (status flipped to
        'expired' on this same row - nothing deleted, everything about the
        card and its history stays intact) before this is checked."""
        return self.status == 'active'

    def refresh_status(self):
        """Soft-expires this card if its expiry_date has passed - there's no
        scheduled job at this project's scale, so this self-heals lazily
        whenever the card is actually read (dashboard, My Card, the public
        QR-scan page, the admin detail view). Never deletes or loses any
        data, just flips status the same way a manual revoke does.
        """
        if self.status == 'active' and self.expiry_date and self.expiry_date < timezone.now().date():
            self.status = 'expired'
            self.save(update_fields=['status'])
        return self.status


class CardActivity(models.Model):
    """Track card activities (view, download, etc)."""
    student_card = models.ForeignKey(StudentCard, on_delete=models.CASCADE, related_name='activities')
    activity_type = models.CharField(max_length=20, choices=[
        ('viewed', 'Viewed'),
        ('downloaded', 'Downloaded'),
        ('verified', 'Verified'),
        ('revoked', 'Revoked'),
        ('reissued', 'Reissued'),
        ('reactivated', 'Reactivated'),
    ])
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)
    # Which staff member performed a revoke/reissue - null for
    # student-initiated events (viewed/downloaded/verified) where there's no
    # staff actor. This is the minimum needed for a staff-facing audit trail
    # ("who revoked this card, and when").
    performed_by = models.ForeignKey(
        StudentUser, null=True, blank=True, on_delete=models.SET_NULL, related_name='card_actions_performed'
    )
    
    class Meta:
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.activity_type} - {self.timestamp}"


class SignupMetrics(models.Model):
    """Track signup performance metrics."""
    user = models.ForeignKey(StudentUser, on_delete=models.CASCADE, related_name='signup_metrics')
    signup_start_time = models.DateTimeField()
    signup_end_time = models.DateTimeField()
    signup_duration_seconds = models.FloatField()  # Time taken to complete signup
    file_upload_size_mb = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Signup Metrics'
    
    def __str__(self):
        return f"Signup - {self.user.email} ({self.signup_duration_seconds:.2f}s)"


class OCRMetrics(models.Model):
    """Track OCR performance and accuracy."""
    user = models.ForeignKey(StudentUser, on_delete=models.CASCADE, related_name='ocr_metrics')
    ocr_start_time = models.DateTimeField()
    ocr_end_time = models.DateTimeField()
    ocr_duration_seconds = models.FloatField()  # Time taken for OCR processing
    ocr_confidence_score = models.FloatField(default=0)  # 0-100 confidence percentage
    extracted_student_number = models.CharField(max_length=20, null=True, blank=True)
    extracted_full_name = models.CharField(max_length=255, null=True, blank=True)
    is_accurate = models.BooleanField(default=False)  # Was OCR result accurate?
    manual_correction_required = models.BooleanField(default=False)
    document_quality_score = models.FloatField(default=0)  # 0-100 document quality
    document_classification_confidence = models.FloatField(default=0)  # 0-100 "is this a TUT proof of registration" confidence
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'OCR Metrics'

    def __str__(self):
        return f"OCR - {self.user.email} ({self.ocr_duration_seconds:.2f}s, {self.ocr_confidence_score:.1f}%)"


class PageVisit(models.Model):
    """Lightweight site-wide traffic log, used for the admin reporting page."""
    user = models.ForeignKey(StudentUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='page_visits')
    path = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.path} - {self.timestamp}"


