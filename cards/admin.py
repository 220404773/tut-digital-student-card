from django.contrib import admin
from .models import StudentCard


@admin.register(StudentCard)
class StudentCardAdmin(admin.ModelAdmin):
    list_display = ('card_number', 'user', 'status', 'issue_date', 'expiry_date')
    list_filter = ('status', 'issue_date')
    search_fields = ('card_number', 'user__email', 'user__student_number')
    readonly_fields = ('card_number', 'issue_date')
