from django.contrib import admin
from django.urls import path
from django.template.response import TemplateResponse
from django.utils.html import format_html
from .models import StudentCard, CardActivity, SignupMetrics, OCRMetrics, PageVisit


@admin.register(StudentCard)
class StudentCardAdmin(admin.ModelAdmin):
    list_display = ('card_number', 'user', 'status', 'issue_date', 'expiry_date')
    list_filter = ('status', 'issue_date')
    search_fields = ('card_number', 'user__email', 'user__student_number')
    readonly_fields = ('card_number', 'issue_date')


@admin.register(CardActivity)
class CardActivityAdmin(admin.ModelAdmin):
    list_display = ('student_card', 'activity_type', 'timestamp', 'ip_address')
    list_filter = ('activity_type', 'timestamp')
    search_fields = ('student_card__card_number', 'ip_address')
    readonly_fields = ('timestamp',)


@admin.register(SignupMetrics)
class SignupMetricsAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'signup_duration_display', 'file_size_display', 'created_date')
    list_filter = ('created_at',)
    search_fields = ('user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('signup_start_time', 'signup_end_time', 'signup_duration_seconds', 
                       'file_upload_size_mb', 'file_upload_time_seconds', 'created_at')
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'Email'
    
    def signup_duration_display(self, obj):
        return f"{obj.signup_duration_seconds:.2f}s"
    signup_duration_display.short_description = 'Duration'
    
    def file_size_display(self, obj):
        if obj.file_upload_size_mb:
            return f"{obj.file_upload_size_mb:.2f} MB"
        return "-"
    file_size_display.short_description = 'File Size'
    
    def created_date(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M:%S')
    created_date.short_description = 'Created At'


@admin.register(OCRMetrics)
class OCRMetricsAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'confidence_display', 'duration_display', 'accuracy_badge', 'created_date')
    list_filter = ('is_accurate', 'manual_correction_required', 'created_at')
    search_fields = ('user__email', 'extracted_student_number', 'extracted_full_name')
    readonly_fields = ('ocr_start_time', 'ocr_end_time', 'ocr_duration_seconds',
                       'ocr_confidence_score', 'created_at')
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'Email'
    
    def confidence_display(self, obj):
        color = 'green' if obj.ocr_confidence_score >= 90 else 'orange' if obj.ocr_confidence_score >= 70 else 'red'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color,
            obj.ocr_confidence_score
        )
    confidence_display.short_description = 'Confidence'
    
    def duration_display(self, obj):
        return f"{obj.ocr_duration_seconds:.2f}s"
    duration_display.short_description = 'Duration'
    
    def accuracy_badge(self, obj):
        if obj.is_accurate:
            return format_html('<span style="background-color: #28a745; color: white; padding: 3px 8px; border-radius: 3px;">Accurate</span>')
        elif obj.manual_correction_required:
            return format_html('<span style="background-color: #fd7e14; color: white; padding: 3px 8px; border-radius: 3px;">Needs Review</span>')
        return format_html('<span style="background-color: #dc3545; color: white; padding: 3px 8px; border-radius: 3px;">Inaccurate</span>')
    accuracy_badge.short_description = 'Status'

    def created_date(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M:%S')
    created_date.short_description = 'Created At'


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    list_display = ('path', 'timestamp', 'ip_address')
    list_filter = ('timestamp',)
    search_fields = ('path', 'ip_address')
