import logging
import re
from collections import Counter

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse, reverse_lazy
from django.utils import timezone
from cards.models import StudentCard, CardActivity
from cards.utils import generate_unique_card_number, issue_card_if_allowed
from .models import User, DeletedStudentLog, ReviewRequest
from .views import delete_registration_proof

logger = logging.getLogger(__name__)

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
        """Returns 'reissued', 'issued', 'awaiting_email', 'not_verified' or 'failed'."""
        card = StudentCard.objects.filter(user=student).first()
        if not card:
            # No card yet - issue one under the same rule as every other path
            # (verified, and email confirmed), rather than silently doing nothing.
            try:
                return issue_card_if_allowed(student, request)
            except Exception:
                logger.exception("Card generation failed for student %s", student.pk)
                return 'failed'

        # A new card number is enough: the QR code is drawn from it each time
        # it's shown, so the old QR code stops working with no file to clean up.
        card.card_number = generate_unique_card_number(student)
        card.status = 'active'
        # Restore the expiry from the student's actual proof of registration,
        # not whatever was already on the card - otherwise reissuing an
        # expired card would leave expiry_date in the past and it would
        # immediately flip back to 'expired' on the next refresh_status().
        card.expiry_date = student.registration_end_date or card.expiry_date
        card.save()
        CardActivity.objects.create(student_card=card, activity_type='reissued', performed_by=request.user)
        return 'reissued'

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
        outcomes = Counter(self._reissue_single_card(student, request) for student in queryset)
        message = f"Reissued {outcomes['reissued']} card(s), newly issued {outcomes['issued']} card(s)."
        if outcomes['awaiting_email']:
            message += f" {outcomes['awaiting_email']} student(s) will get their card once they confirm their email."
        if outcomes['not_verified']:
            message += f" {outcomes['not_verified']} student(s) aren't verified, so no card was issued."
        if outcomes['failed']:
            message += f" Failed to issue a card for {outcomes['failed']} student(s)."
        self.message_user(request, message)

    def get_urls(self):
        custom_urls = [
            path(
                '<int:student_id>/revoke-card/',
                self.admin_site.admin_view(self.revoke_card_view),
                name='accounts_user_revoke_card',
            ),
            path(
                '<int:student_id>/reissue-card/',
                self.admin_site.admin_view(self.reissue_card_view),
                name='accounts_user_reissue_card',
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
        return redirect('admin:accounts_user_change', student.pk)

    def reissue_card_view(self, request, student_id):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        student = get_object_or_404(User, pk=student_id)
        outcome = self._reissue_single_card(student, request)
        name = student.get_full_name_display()
        if outcome == 'reissued':
            self.message_user(request, f"Reissued {name}'s digital card (new card number and QR code).")
        elif outcome == 'issued':
            self.message_user(request, f'{name} had no card yet - issued one.')
        elif outcome == 'awaiting_email':
            self.message_user(request, f'{name} will get their card once they confirm their email.', level=messages.WARNING)
        elif outcome == 'not_verified':
            self.message_user(request, f"{name} isn't verified, so no card was issued.", level=messages.WARNING)
        else:
            self.message_user(request, f"Couldn't issue a card for {name}.", level=messages.ERROR)
        return redirect('admin:accounts_user_change', student.pk)

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

        # Ticking "is_verified" here is a manual approval - equivalent to the
        # OCR auto-verification path in signup(), so the card follows the same
        # rule: issued now, or once the student confirms their email.
        if obj.is_verified and not obj.is_staff:
            # A staff member has just decided, so nothing is left in the
            # review queue for this student.
            closed = ReviewRequest.objects.filter(user=obj, status='pending').update(
                status='approved', resolved_at=timezone.now(), resolved_by=request.user,
                staff_note='Closed: verified by a staff member in the admin.',
            )
            if closed:
                self.message_user(request, 'Their pending review request was closed.')
            try:
                outcome = issue_card_if_allowed(obj, request)
                if outcome == 'issued':
                    self.message_user(request, 'Digital student card generated for this student.')
                elif outcome == 'awaiting_email':
                    self.message_user(request, 'Student verified - their card will be issued once they confirm their email.')
            except Exception as e:
                self.message_user(request, f'Error generating card: {str(e)}', level=messages.ERROR)


admin.site.register(User, StudentUserAdmin)


@admin.register(ReviewRequest)
class ReviewRequestAdmin(admin.ModelAdmin):
    """Read-mostly view; decisions are made on the Review Requests report,
    which also verifies the student and issues their card."""
    list_display = ('user', 'reason', 'status', 'created_at', 'resolved_by', 'resolved_at')
    list_filter = ('status', 'reason')
    search_fields = ('user__student_number', 'user__email')
    readonly_fields = ('user', 'reason', 'message', 'created_at', 'resolved_at', 'resolved_by')
admin.site.unregister(Group)

# The "View site" link in the admin header (admin/base.html's {{ site_url }})
# defaults to "/" - staff using this admin have no reason to land on the
# public homepage, so send it straight to the reporting dashboard instead.
admin.site.site_url = reverse_lazy('performance_dashboard')
