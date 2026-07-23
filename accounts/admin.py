from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from cards.models import StudentCard
from cards.utils import generate_student_card
from .models import StudentUser


class StudentUserAdmin(UserAdmin):
    model = StudentUser
    list_display = ('email', 'first_name', 'last_name', 'student_number', 'is_verified', 'is_staff')
    # Overrides UserAdmin.fieldsets rather than extending it, dropping
    # groups/user_permissions - admin accounts here are just is_staff +
    # is_superuser, no per-permission management needed.
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
        ('Student Info', {'fields': ('student_number', 'phone', 'date_of_birth', 'gender', 'profile_picture')}),
        ('Registration', {'fields': ('registration_proof', 'is_verified', 'registration_end_date')}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # Ticking "is_verified" here is a manual/soft approval - equivalent
        # to the OCR auto-verification path in signup(), so it should
        # generate the student's card the same way. generate_student_card
        # already no-ops if a card exists, so this is safe to call on
        # every save rather than only on the True transition.
        if obj.is_verified and not StudentCard.objects.filter(user=obj).exists():
            try:
                generate_student_card(obj, request)
                self.message_user(request, 'Digital student card generated for this student.')
            except Exception as e:
                self.message_user(request, f'Error generating card: {str(e)}', level=messages.ERROR)


admin.site.register(StudentUser, StudentUserAdmin)
admin.site.unregister(Group)
