import re

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse, reverse_lazy
from cards.models import StudentCard, CardActivity
from cards.utils import generate_student_card, generate_unique_card_number, generate_qr_code_for_card
from .models import User, DeletedStudentLog
from .views import delete_registration_proof

STAFF_ADMIN_NUMBER_RE = re.compile(r'^STAFF-ADMIN-(\d+)$')


class StudentUserAdmin(UserAdmin):
    model = User
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
    actions = ['revoke_card_action', 'reissue_card_action']
    # "View on site" normally links to the object's own get_absolute_url()
    # (User has none) - point it at the admin reporting dashboard
    # instead, since that's the natural "site" a staff member editing a
    # student wants to jump to from here.
    view_on_site = lambda self, obj: reverse('performance_dashboard')

    def _revoke_single_card(self, student, performed_by):
        """Returns True if the student had a card to revoke."""
        card = StudentCard.objects.filter(user=student).first()
        if not card:
            return False
        card.status = 'revoked'
        card.save()
        CardActivity.objects.create(student_card=card, activity_type='revoked', performed_by=performed_by)
        return True

    def _reissue_single_card(self, student, request):
        """Returns (reissued, newly_issued, failed) - exactly one is True."""
        card = StudentCard.objects.filter(user=student).first()
        if not card:
            # No card yet - issue one the same way OCR/manual verification does,
            # rather than silently doing nothing.
            try:
                generate_student_card(student, request)
                return False, True, False
            except Exception:
                return False, False, True

        # The new QR filename is based on the new card number, so it won't
        # overwrite the old one - without deleting it explicitly, every
        # reissue would leave the previous QR image orphaned in storage forever.
        old_qr_code = card.qr_code
        card.card_number = generate_unique_card_number(student)
        card.status = 'active'
        # Restore the expiry from the student's actual proof of registration,
        # not whatever was already on the card - otherwise reissuing an
        # expired card would leave expiry_date in the past and it would
        # immediately flip back to 'expired' on the next refresh_status().
        card.expiry_date = student.registration_end_date or card.expiry_date
        card.save()
        generate_qr_code_for_card(card, request)
        if old_qr_code:
            old_qr_code.storage.delete(old_qr_code.name)
        CardActivity.objects.create(student_card=card, activity_type='reissued', performed_by=request.user)
        return True, False, False

    @admin.action(description='Revoke digital card')
    def revoke_card_action(self, request, queryset):
        revoked = sum(1 for student in queryset if self._revoke_single_card(student, request.user))
        skipped = queryset.count() - revoked

        message = f'Revoked {revoked} card(s).'
        if skipped:
            message += f' {skipped} selected student(s) had no card to revoke.'
        self.message_user(request, message)

    @admin.action(description='Reissue digital card (new card number + QR code)')
    def reissue_card_action(self, request, queryset):
        reissued = newly_issued = failed = 0
        for student in queryset:
            r, n, f = self._reissue_single_card(student, request)
            reissued += r
            newly_issued += n
            failed += f

        message = f'Reissued {reissued} card(s), newly issued {newly_issued} card(s).'
        if failed:
            message += f' Failed to issue a card for {failed} student(s).'
        self.message_user(request, message)

    def get_urls(self):
        custom_urls = [
            path(
                '<int:student_id>/revoke-card/',
                self.admin_site.admin_view(self.revoke_card_view),
                name='accounts_studentuser_revoke_card',
            ),
            path(
                '<int:student_id>/reissue-card/',
                self.admin_site.admin_view(self.reissue_card_view),
                name='accounts_studentuser_reissue_card',
            ),
        ]
        return custom_urls + super().get_urls()

    def revoke_card_view(self, request, student_id):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        student = get_object_or_404(User, pk=student_id)
        if self._revoke_single_card(student, request.user):
            self.message_user(request, f"Revoked {student.get_full_name_display()}'s digital card.")
        else:
            self.message_user(request, f'{student.get_full_name_display()} has no card to revoke.', level=messages.WARNING)
        return redirect('admin:accounts_studentuser_change', student.pk)

    def reissue_card_view(self, request, student_id):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        student = get_object_or_404(User, pk=student_id)
        reissued, newly_issued, failed = self._reissue_single_card(student, request)
        if reissued:
            self.message_user(request, f"Reissued {student.get_full_name_display()}'s digital card (new card number and QR code).")
        elif newly_issued:
            self.message_user(request, f'{student.get_full_name_display()} had no card yet - issued one.')
        else:
            self.message_user(request, f"Couldn't issue a card for {student.get_full_name_display()}.", level=messages.ERROR)
        return redirect('admin:accounts_studentuser_change', student.pk)

    def _log_deletion(self, obj, deleted_by):
        # Deleting a User cascades - their StudentCard, CardActivity,
        # and metrics rows all disappear with it. Capture a snapshot before
        # that happens, or there'd be no record the student ever existed.
        card = StudentCard.objects.filter(user=obj).first()
        DeletedStudentLog.objects.create(
            student_number=obj.student_number,
            full_name=obj.get_full_name_display(),
            email=obj.email,
            was_verified=obj.is_verified,
            had_card=card is not None,
            card_number=card.card_number if card else '',
            registration_end_date=obj.registration_end_date,
            was_email_confirmed=obj.email_confirmed,
            deleted_by=deleted_by,
        )

    def delete_model(self, request, obj):
        self._log_deletion(obj, request.user)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._log_deletion(obj, request.user)
        super().delete_queryset(request, queryset)

    def save_model(self, request, obj, form, change):
        # Staff+superuser accounts aren't real students - give them a
        # placeholder student_number instead of requiring a real one.
        # student_number is unique, so each admin gets its own numbered
        # placeholder (STAFF-ADMIN-1, -2, ...) rather than colliding on a
        # single shared value. Only assigns a new one if not already using
        # this placeholder scheme, so it doesn't reassign on every save.
        if obj.is_staff and obj.is_superuser and not STAFF_ADMIN_NUMBER_RE.match(obj.student_number or ''):
            used_numbers = set()
            for number in User.objects.filter(
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

        # The "Registration" fieldset exposes registration_proof directly -
        # if a staff member uploads one here, there's no OCR step to trigger
        # the usual cleanup, so it would otherwise sit in storage
        # indefinitely. This field is never meant to persist either way.
        if obj.registration_proof:
            delete_registration_proof(obj)

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


admin.site.register(User, StudentUserAdmin)
admin.site.unregister(Group)

# The "View site" link in the admin header (admin/base.html's {{ site_url }})
# defaults to "/" - staff using this admin have no reason to land on the
# public homepage, so send it straight to the reporting dashboard instead.
admin.site.site_url = reverse_lazy('performance_dashboard')
