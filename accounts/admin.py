import re

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from cards.models import StudentCard
from cards.utils import generate_student_card
from .models import StudentUser

STAFF_ADMIN_NUMBER_RE = re.compile(r'^STAFF-ADMIN-(\d+)$')


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
        # Staff+superuser accounts aren't real students - give them a
        # placeholder student_number instead of requiring a real one.
        # student_number is unique, so each admin gets its own numbered
        # placeholder (STAFF-ADMIN-1, -2, ...) rather than colliding on a
        # single shared value. Only assigns a new one if not already using
        # this placeholder scheme, so it doesn't reassign on every save.
        if obj.is_staff and obj.is_superuser and not STAFF_ADMIN_NUMBER_RE.match(obj.student_number or ''):
            used_numbers = set()
            for number in StudentUser.objects.filter(
                student_number__startswith='STAFF-ADMIN-'
            ).exclude(pk=obj.pk).values_list('student_number', flat=True):
                match = STAFF_ADMIN_NUMBER_RE.match(number)
                if match:
                    used_numbers.add(int(match.group(1)))
            next_number = 1
            while next_number in used_numbers:
                next_number += 1
            obj.student_number = f'STAFF-ADMIN-{next_number}'

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
