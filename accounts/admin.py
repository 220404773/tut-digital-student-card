from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import StudentUser


class StudentUserAdmin(UserAdmin):
    model = StudentUser
    list_display = ('email', 'first_name', 'last_name', 'student_number', 'is_verified')
    fieldsets = UserAdmin.fieldsets + (
        ('Student Info', {'fields': ('student_number', 'phone', 'date_of_birth', 'gender', 'profile_picture')}),
        ('Registration', {'fields': ('registration_proof', 'is_verified', 'registration_end_date')}),
    )


admin.site.register(StudentUser, StudentUserAdmin)
